#!/usr/bin/env python3
"""Provider-agnostic image generation with health-based routing and failover.

The rest of Content Machine asks for images without knowing where they come
from. Four providers sit behind one interface, tried in a configured order:

    comfyui     the workstation GPU. Preferred: only provider that produces
                depicted imagery at quality. Absent whenever the PC is off.
    procedural  ffmpeg-only abstract plates (scripts/make_visuals.py). Always
                available on the VPS - no GPU, no network, no cost. Cannot
                produce depicted imagery and never claims to.
    gemini      Google's image models over plain HTTPS. The only depicted
                route that works with no GPU, and therefore what the VPS
                falls back to when the workstation is off. Metered, so it
                needs GEMINI_IMAGE_ENABLED on top of a key.
    api         an external HTTP provider, for whichever vendor is chosen
                later. Disabled unless explicitly configured.

Both paid providers sit after every free one and are inert without deliberate
configuration, so an unattended run cannot start spending money on its own.

Why this is safe to fail over: a procedural plate is stamped at generation,
and project.py's publication gate inspects the artefacts themselves. Falling
back to `procedural` degrades quality but cannot slip a placeholder past
review as a production asset - the gate blocks it. Failover therefore never
trades correctness for availability.

Requests that need depicted imagery set ``require_depicted``; providers that
cannot satisfy that are skipped rather than used to produce a result which
would be rejected downstream anyway.

Configuration is entirely environment-driven (see .env.example). There are no
default endpoints: an unset COMFYUI_URL means "no workstation", not localhost.

    python3 scripts/generation.py providers
    python3 scripts/generation.py generate --prompt "..." --out DIR [--count N]
"""
import argparse
import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_visuals  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("generation")

ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT / "jobs"
PROVIDER_STATE_PATH = JOBS_DIR / "_provider_state.json"

# Order matters: free providers first (workstation, then always-on local),
# metered ones last. Last place is deliberate - a provider that costs money
# must never be reached by accident just because something earlier was
# briefly unhealthy. `gemini` precedes `api` only because it has a working
# default; both are equally opt-in.
DEFAULT_ORDER = ("comfyui", "procedural", "gemini", "api")

DEFAULT_HEALTH_TIMEOUT = 5.0
DEFAULT_GENERATE_TIMEOUT = 300.0
DEFAULT_COOLDOWN_SECONDS = 300.0
DEFAULT_ATTEMPTS = 2

# Terminal and non-terminal job states.
QUEUED, RUNNING, RETRYING, COMPLETED, FAILED = (
    "queued", "running", "retrying", "completed", "failed")


class GenerationError(Exception):
    """Raised when no provider could satisfy a request."""

    def __init__(self, message, attempts=None):
        super().__init__(message)
        self.attempts = attempts or []


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _env(name, default=None):
    value = os.environ.get(name, "")
    return value.strip() or default


def _env_float(name, default):
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, raw, default)
        return default


# --- Request ---------------------------------------------------------------

class GenerationRequest:
    """What to generate. Provider-neutral by construction.

    ``require_depicted`` is the one field with routing consequences: it means
    the caller needs a real depicted scene, so providers that can only emit
    abstract plates are not candidates.
    """

    def __init__(self, prompt, negative_prompt=None, width=1920, height=1080,
                 count=1, seed=20260827, model=None, style="deep-night",
                 require_depicted=False, params=None):
        self.prompt = prompt
        self.negative_prompt = negative_prompt or ""
        self.width = int(width)
        self.height = int(height)
        self.count = int(count)
        self.seed = int(seed)
        self.model = model
        self.style = style
        self.require_depicted = bool(require_depicted)
        self.params = dict(params or {})

    def to_dict(self):
        return {
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
            "count": self.count,
            "seed": self.seed,
            "model": self.model,
            "style": self.style,
            "require_depicted": self.require_depicted,
            "params": self.params,
        }

    def digest(self):
        """Stable hash of the request, used as the idempotency key.

        Two identical requests produce the same job id, so a retried or
        resumed run reuses the completed job instead of generating - and
        paying for - the same assets twice.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# --- Providers -------------------------------------------------------------

class Provider:
    """Common provider interface. Subclasses own all vendor specifics."""

    name = "base"
    produces_depicted = False
    costs_money = False

    def configured(self):
        raise NotImplementedError

    def health(self, timeout=DEFAULT_HEALTH_TIMEOUT):
        """Return ``(ok, detail)``. Must never raise."""
        raise NotImplementedError

    def generate(self, request, out_dir, timeout=DEFAULT_GENERATE_TIMEOUT):
        """Produce ``request.count`` images into ``out_dir``.

        Returns a dict with at least ``assets`` (list of Path). May also
        return ``provider_job_id``, ``model``, ``cost_usd``.
        """
        raise NotImplementedError


class ProceduralProvider(Provider):
    """ffmpeg-only abstract plates. The always-on VPS provider.

    Needs no GPU, no network and no credentials, which is exactly why it is
    the reliability floor: if this cannot run, the host cannot render either.
    It is honest about its limits - it does not produce depicted imagery and
    declares so, and every plate it writes is provenance-stamped.
    """

    name = "procedural"
    produces_depicted = False
    costs_money = False

    def configured(self):
        return True

    def health(self, timeout=DEFAULT_HEALTH_TIMEOUT):
        if not shutil.which("ffmpeg"):
            return False, "ffmpeg not on PATH"
        return True, "ffmpeg available"

    def generate(self, request, out_dir, timeout=DEFAULT_GENERATE_TIMEOUT):
        style = request.style if request.style in make_visuals.STYLES else "deep-night"
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"gen_{request.digest()[:8]}"
        assets = []
        for i in range(request.count):
            path = out_dir / f"{prefix}_{i + 1:02d}.png"
            make_visuals.build_still(style, i, path, request.seed)
            make_visuals.stamp_provenance(path, style, request.seed, i)
            luma = make_visuals.measure_luma(path)
            if luma is not None and luma < make_visuals.MIN_MEAN_LUMA:
                raise GenerationError(
                    f"{path.name} is below the QC luminance floor "
                    f"({luma:.1f} < {make_visuals.MIN_MEAN_LUMA})")
            assets.append(path)
        return {
            "assets": assets,
            "model": f"procedural/{style}",
            "cost_usd": 0.0,
            "notes": "procedural abstract plates; not depicted imagery",
        }


class ComfyUIProvider(Provider):
    """The workstation's ComfyUI. Preferred whenever it answers.

    All ComfyUI specifics - the prompt graph, the polling protocol, the view
    endpoint - stay inside this adapter. The workflow itself is a template
    file so the application is not welded to one graph.
    """

    name = "comfyui"
    produces_depicted = True
    costs_money = False

    # Placeholders substituted into the workflow template.
    PLACEHOLDERS = ("%prompt%", "%negative%", "%width%", "%height%", "%seed%", "%model%")

    def __init__(self, url=None, workflow_path=None):
        self.url = (url if url is not None else _env("COMFYUI_URL") or "").rstrip("/")
        self.workflow_path = workflow_path or _env(
            "COMFYUI_WORKFLOW", str(ROOT / "config" / "comfyui_workflow.example.json"))

    def configured(self):
        return bool(self.url)

    def health(self, timeout=DEFAULT_HEALTH_TIMEOUT):
        if not self.configured():
            return False, "COMFYUI_URL is not set"
        try:
            with urllib.request.urlopen(
                    f"{self.url}/system_stats", timeout=timeout) as response:
                if response.status != 200:
                    return False, f"HTTP {response.status} from /system_stats"
                json.loads(response.read().decode("utf-8"))
            return True, f"reachable at {self.url}"
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as e:
            # Includes the PC simply being off, which is an expected state,
            # not an error worth a stack trace.
            return False, f"unreachable: {type(e).__name__}: {e}"

    def _load_workflow(self, request):
        path = Path(self.workflow_path)
        if not path.is_file():
            raise GenerationError(f"ComfyUI workflow template not found: {path}")
        raw = path.read_text()
        substitutions = {
            "%prompt%": request.prompt,
            "%negative%": request.negative_prompt,
            "%width%": str(request.width),
            "%height%": str(request.height),
            "%seed%": str(request.seed),
            "%model%": request.model or _env("COMFYUI_MODEL", "sd_xl_base_1.0.safetensors"),
        }
        for token, value in substitutions.items():
            # json.dumps then strip the quotes: escapes quotes/newlines in a
            # user prompt so an apostrophe cannot corrupt the graph.
            raw = raw.replace(token, json.dumps(str(value))[1:-1])
        try:
            graph = json.loads(raw)
        except json.JSONDecodeError as e:
            raise GenerationError(f"workflow template is not valid JSON after substitution: {e}")
        # Underscore keys are template documentation, not nodes; ComfyUI would
        # reject them for having no class_type.
        return {k: v for k, v in graph.items() if not k.startswith("_")}

    def _post(self, endpoint, payload, timeout):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}{endpoint}", data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def generate(self, request, out_dir, timeout=DEFAULT_GENERATE_TIMEOUT):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        workflow = self._load_workflow(request)
        client_id = f"content-machine-{request.digest()}"

        try:
            submitted = self._post("/prompt", {"prompt": workflow, "client_id": client_id},
                                   min(timeout, 30.0))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GenerationError(f"ComfyUI rejected the workflow: {e}")
        prompt_id = submitted.get("prompt_id")
        if not prompt_id:
            raise GenerationError(f"ComfyUI returned no prompt_id: {submitted}")

        outputs = self._await_outputs(prompt_id, timeout)
        assets = self._download(outputs, out_dir, request)
        if not assets:
            raise GenerationError(f"ComfyUI job {prompt_id} produced no images")
        return {
            "assets": assets,
            "provider_job_id": prompt_id,
            # The model actually submitted, resolved the same way the workflow
            # substitution resolves it - so provenance matches what ran.
            "model": (request.model or _env("COMFYUI_MODEL")
                      or "sd_xl_base_1.0.safetensors"),
            "cost_usd": 0.0,
            "notes": f"generated on ComfyUI at {self.url}",
        }

    def _await_outputs(self, prompt_id, timeout):
        """Poll /history until the job appears, bounded by ``timeout``."""
        deadline = time.monotonic() + timeout
        interval = 1.0
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                        f"{self.url}/history/{prompt_id}",
                        timeout=DEFAULT_HEALTH_TIMEOUT) as response:
                    history = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError):
                # The PC can drop out mid-job; keep polling until the deadline
                # so a brief blip does not discard an in-flight render.
                time.sleep(interval)
                continue
            entry = history.get(prompt_id)
            if entry:
                status = (entry.get("status") or {})
                if status.get("status_str") == "error":
                    raise GenerationError(f"ComfyUI job {prompt_id} failed: {status}")
                images = []
                for node in (entry.get("outputs") or {}).values():
                    images.extend(node.get("images") or [])
                if images:
                    return images
                if status.get("completed"):
                    raise GenerationError(
                        f"ComfyUI job {prompt_id} completed with no images")
            time.sleep(interval)
            interval = min(interval * 1.5, 5.0)
        raise GenerationError(f"ComfyUI job {prompt_id} timed out after {timeout:.0f}s")

    def _download(self, images, out_dir, request):
        assets = []
        for i, image in enumerate(images[:request.count]):
            query = urllib.parse.urlencode({
                "filename": image.get("filename", ""),
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            })
            suffix = Path(image.get("filename", "out.png")).suffix or ".png"
            path = out_dir / f"gen_{request.digest()[:8]}_{i + 1:02d}{suffix}"
            try:
                with urllib.request.urlopen(
                        f"{self.url}/view?{query}", timeout=60.0) as response:
                    path.write_bytes(response.read())
            except (urllib.error.URLError, OSError) as e:
                raise GenerationError(f"could not retrieve {image.get('filename')}: {e}")
            assets.append(path)
        return assets


class ApiProvider(Provider):
    """Generic external HTTP image API. Off unless explicitly configured.

    Deliberately last in the routing order and inert without credentials, so
    an unattended pipeline cannot start incurring charges on its own. The
    request/response shape is configurable because this stands in for
    whichever vendor is chosen later; nothing here is vendor-specific.
    """

    name = "api"
    produces_depicted = True
    costs_money = True

    def __init__(self, url=None, api_key=None, model=None):
        self.url = url if url is not None else _env("IMAGE_API_URL")
        self.api_key = api_key if api_key is not None else _env("IMAGE_API_KEY")
        self.model = model if model is not None else _env("IMAGE_API_MODEL")

    def configured(self):
        return bool(self.url and self.api_key)

    def health(self, timeout=DEFAULT_HEALTH_TIMEOUT):
        if not self.configured():
            return False, "IMAGE_API_URL/IMAGE_API_KEY not set (external generation disabled)"
        # No probe request: an availability ping to a metered endpoint can
        # itself cost money. Configuration is the only free signal.
        return True, f"configured: {urllib.parse.urlsplit(self.url).netloc}"

    def generate(self, request, out_dir, timeout=DEFAULT_GENERATE_TIMEOUT):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "width": request.width,
            "height": request.height,
            "n": request.count,
            "seed": request.seed,
        }
        if self.model:
            payload["model"] = self.model
        payload.update(request.params.get("api", {}))
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GenerationError(f"image API request failed: {e}")

        assets = self._extract(body, out_dir, request)
        if not assets:
            raise GenerationError("image API returned no usable image data")
        return {
            "assets": assets,
            "provider_job_id": body.get("id"),
            "model": body.get("model") or self.model,
            "cost_usd": body.get("cost_usd"),
            "notes": "generated by external image API",
        }

    def _extract(self, body, out_dir, request):
        """Pull images out of a response, accepting base64 or URL delivery."""
        items = body.get("data") or body.get("images") or []
        if isinstance(items, dict):
            items = [items]
        assets = []
        for i, item in enumerate(items[:request.count]):
            path = out_dir / f"gen_{request.digest()[:8]}_{i + 1:02d}.png"
            if isinstance(item, str):
                item = {"b64_json": item}
            blob = item.get("b64_json") or item.get("b64")
            url = item.get("url")
            try:
                if blob:
                    path.write_bytes(base64.b64decode(blob))
                elif url:
                    with urllib.request.urlopen(url, timeout=60.0) as response:
                        path.write_bytes(response.read())
                else:
                    continue
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise GenerationError(f"could not materialise image {i + 1}: {e}")
            assets.append(path)
        return assets


# --- Gemini image API ------------------------------------------------------

# The vendor's public endpoint. Overridable so tests can aim the adapter at a
# local stand-in. This is not the thing COMFYUI_URL forbids defaulting: that
# rule protects "whose machine", and an unset workstation URL must not become
# localhost. This names *which service*, which is part of the adapter's
# identity - and the adapter is inert without GEMINI_IMAGE_ENABLED, so having
# a default here cannot make an unattended run call anything.
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_DEFAULT_MODEL = "gemini-3.1-flash-image"

# What the API accepts. The closest of these to the requested pixel ratio is
# sent, because the API takes a ratio, not a width and height.
_GEMINI_ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
                         "9:16", "16:9", "21:9")

# Only what render.py can actually read (SUPPORTED_IMAGE_EXTENSIONS).
_GEMINI_MIME_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg"}

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _closest_aspect_ratio(width, height):
    target = (width or 1) / (height or 1)

    def distance(ratio):
        w, h = ratio.split(":")
        return abs(int(w) / int(h) - target)

    return min(_GEMINI_ASPECT_RATIOS, key=distance)


class GeminiProvider(Provider):
    """Google's Gemini image models, over plain HTTPS.

    Why this exists: the VPS has no GPU, so when the workstation is off there
    is no route to *depicted* imagery at all, and every concept with
    ``procedural_visuals_acceptable=false`` halts at asset generation. This is
    that route.

    It is metered, so it is gated twice. ``GEMINI_API_KEY`` is already set for
    the creative brief (scripts/creative.py), and that key must not quietly
    start buying images as well - so a key alone does not enable this.
    ``GEMINI_IMAGE_ENABLED`` is a separate, deliberate act of consenting to
    spend, and without it the provider is skipped exactly like an absent one.

    Stdlib-only on purpose: the call is a JSON POST, so unlike creative.py's
    LLM call this adds no SDK to the generation path.
    """

    name = "gemini"
    produces_depicted = True
    costs_money = True

    def __init__(self, api_key=None, model=None, enabled=None, base_url=None,
                 image_size=None):
        self.api_key = api_key if api_key is not None else (
            _env("GEMINI_IMAGE_API_KEY") or _env("GEMINI_API_KEY"))
        self.model = model if model is not None else _env(
            "GEMINI_IMAGE_MODEL", GEMINI_DEFAULT_MODEL)
        self.enabled = enabled if enabled is not None else (
            _env("GEMINI_IMAGE_ENABLED", "").lower() in _TRUTHY)
        self.base_url = (base_url if base_url is not None else
                         _env("GEMINI_API_BASE", GEMINI_API_BASE)).rstrip("/")
        # Unset means "whatever the API defaults to". Choosing a larger size
        # on the operator's behalf would be choosing to spend more.
        self.image_size = image_size if image_size is not None else _env("GEMINI_IMAGE_SIZE")

    def configured(self):
        return bool(self.enabled and self.api_key)

    def health(self, timeout=DEFAULT_HEALTH_TIMEOUT):
        if not self.enabled:
            return False, "GEMINI_IMAGE_ENABLED is not set (paid image generation disabled)"
        if not self.api_key:
            return False, "GEMINI_IMAGE_ENABLED is set but GEMINI_IMAGE_API_KEY/GEMINI_API_KEY is not"
        # Config-only, exactly like `api`: every request to this endpoint is
        # metered, so an availability probe would itself cost money.
        return True, f"configured: {self.model}"

    def generate(self, request, out_dir, timeout=DEFAULT_GENERATE_TIMEOUT):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # One image per call: the image models return a single picture per
        # response, so count is a loop rather than a parameter.
        assets, model_used, response_id, tokens = [], None, None, 0
        for i in range(max(1, request.count)):
            body = self._call(self._payload(request), timeout)
            assets.append(self._extract(
                body, out_dir / f"gen_{request.digest()[:8]}_{i + 1:02d}"))
            model_used = body.get("modelVersion") or self.model
            response_id = response_id or body.get("responseId")
            tokens += (body.get("usageMetadata") or {}).get("totalTokenCount") or 0
        return {
            "assets": assets,
            "provider_job_id": response_id,
            "model": model_used or self.model,
            # The API reports tokens, not money. Deriving a dollar figure from
            # a price list this code cannot verify would be a fabricated
            # number in provenance, so the cost is left unknown.
            "cost_usd": None,
            "notes": (f"generated by Gemini image API ({model_used or self.model}); "
                      f"{tokens} token(s) billed; seed not honoured by this API"),
        }

    def _payload(self, request):
        prompt = request.prompt
        if request.negative_prompt:
            # The API has no negative-prompt field. Folding it into the text
            # is the only way it reaches the model at all; dropping it
            # silently would generate images the caller did not ask for.
            prompt = f"{prompt}\n\nDo not include: {request.negative_prompt}"
        image_config = {"aspectRatio": _closest_aspect_ratio(request.width, request.height)}
        if self.image_size:
            image_config["imageSize"] = self.image_size
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"],
                                 "imageConfig": image_config},
        }
        payload.update(request.params.get("gemini", {}))
        return payload

    def _call(self, payload, timeout):
        # The key travels in a header, never the query string, so it cannot
        # end up in an echoed URL, a redirect or a log line.
        req = urllib.request.Request(
            f"{self.base_url}/models/{self.model}:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()[:300]
            raise GenerationError(
                f"Gemini image request failed (HTTP {e.code}): {detail}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GenerationError(f"Gemini image request failed: {e}")

    def _extract(self, body, path_stub):
        """One image out of one response, or a GenerationError explaining why not.

        A refusal arrives as a 200 with no image in it, so an unchecked parse
        would look like success and return zero assets.
        """
        block_reason = (body.get("promptFeedback") or {}).get("blockReason")
        if block_reason:
            raise GenerationError(f"Gemini refused the prompt: {block_reason}")
        candidates = body.get("candidates") or []
        if not candidates:
            raise GenerationError("Gemini returned no candidates")
        candidate = candidates[0]
        for part in (candidate.get("content") or {}).get("parts") or []:
            blob = part.get("inlineData") or part.get("inline_data") or {}
            if not blob.get("data"):
                continue
            mime = blob.get("mimeType") or blob.get("mime_type") or "image/png"
            suffix = _GEMINI_MIME_EXTENSIONS.get(mime)
            if suffix is None:
                raise GenerationError(
                    f"Gemini returned an image type the renderer cannot read: {mime}")
            path = path_stub.with_suffix(suffix)
            try:
                path.write_bytes(base64.b64decode(blob["data"], validate=True))
            except (ValueError, OSError) as e:
                raise GenerationError(f"could not materialise {path.name}: {e}")
            return path
        raise GenerationError(
            "Gemini returned no image data (finishReason="
            f"{candidate.get('finishReason')!r})")


def build_providers():
    """Instantiate every provider. Construction never touches the network."""
    return {p.name: p for p in (ComfyUIProvider(), ProceduralProvider(),
                                GeminiProvider(), ApiProvider())}


# --- Provider health state (cooldowns) -------------------------------------

def _load_provider_state():
    try:
        return json.loads(PROVIDER_STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_provider_state(state):
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROVIDER_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(PROVIDER_STATE_PATH)


def _in_cooldown(state, name, now=None):
    """Whether a provider is still resting after a recent failure.

    Persisted, so a restart does not immediately re-hammer a provider that
    was already known to be down.
    """
    entry = state.get(name)
    if not entry:
        return False, None
    now = now if now is not None else time.time()
    remaining = (entry.get("failed_at", 0) + entry.get("cooldown_seconds", 0)) - now
    return (remaining > 0), max(0.0, remaining)


def _record_failure(state, name, detail, cooldown):
    state[name] = {
        "failed_at": time.time(),
        "cooldown_seconds": cooldown,
        "detail": detail[:300],
        "at": utc_now(),
    }
    _save_provider_state(state)


def _clear_failure(state, name):
    if name in state:
        del state[name]
        _save_provider_state(state)


# --- Job persistence -------------------------------------------------------

def job_path(job_id):
    return JOBS_DIR / f"{job_id}.json"


def load_job(job_id):
    try:
        return json.loads(job_path(job_id).read_text())
    except (OSError, ValueError):
        return None


def save_job(job):
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = job_path(job["job_id"])
    tmp = path.with_suffix(".tmp")
    # Atomic replace: a crash mid-write must not leave an unparseable job.
    tmp.write_text(json.dumps(job, indent=2) + "\n")
    tmp.replace(path)
    return path


def _assets_present(job):
    return bool(job.get("assets")) and all(Path(p).is_file() for p in job["assets"])


# --- Router ----------------------------------------------------------------

class Router:
    """Picks a provider, fails over, and records the attempt on the job.

    Ordering, timeouts, retries and cooldowns are all configurable, but the
    defaults encode the operating rule: prefer the workstation, stay alive on
    the VPS, and only reach a paid API when explicitly configured.
    """

    def __init__(self, providers=None, order=None, health_timeout=None,
                 generate_timeout=None, cooldown_seconds=None, attempts=None):
        self.providers = providers if providers is not None else build_providers()
        configured_order = _env("GENERATION_ORDER")
        if order is not None:
            self.order = tuple(order)
        elif configured_order:
            self.order = tuple(p.strip() for p in configured_order.split(",") if p.strip())
        else:
            self.order = DEFAULT_ORDER
        self.health_timeout = health_timeout if health_timeout is not None else _env_float(
            "GENERATION_HEALTH_TIMEOUT", DEFAULT_HEALTH_TIMEOUT)
        self.generate_timeout = generate_timeout if generate_timeout is not None else _env_float(
            "GENERATION_TIMEOUT", DEFAULT_GENERATE_TIMEOUT)
        self.cooldown_seconds = cooldown_seconds if cooldown_seconds is not None else _env_float(
            "GENERATION_COOLDOWN", DEFAULT_COOLDOWN_SECONDS)
        # Clamp to at least one: GENERATION_ATTEMPTS=0 would otherwise skip
        # every provider silently and look like a routing bug.
        self.attempts = max(1, int(attempts if attempts is not None else _env_float(
            "GENERATION_ATTEMPTS", DEFAULT_ATTEMPTS)))

    def candidates(self, request=None):
        """Providers eligible for this request, in preference order."""
        chosen = []
        for name in self.order:
            provider = self.providers.get(name)
            if provider is None:
                log.warning("unknown provider in routing order: %s", name)
                continue
            if request is not None and request.require_depicted \
                    and not provider.produces_depicted:
                continue
            chosen.append(provider)
        return chosen

    def status(self, request=None):
        """Health of every provider. Read-only; safe to call anytime."""
        state = _load_provider_state()
        report = []
        for provider in self.candidates(request):
            cooling, remaining = _in_cooldown(state, provider.name)
            ok, detail = provider.health(self.health_timeout)
            report.append({
                "provider": provider.name,
                "configured": provider.configured(),
                "healthy": ok,
                "detail": detail,
                "produces_depicted": provider.produces_depicted,
                "costs_money": provider.costs_money,
                "in_cooldown": cooling,
                "cooldown_remaining_seconds": round(remaining, 1) if remaining else 0.0,
            })
        return report

    def generate(self, request, out_dir, job_id=None):
        """Generate, failing over until a provider succeeds.

        Idempotent: an already-completed job with its assets still on disk is
        returned as-is rather than regenerated.
        """
        job_id = job_id or request.digest()
        existing = load_job(job_id)
        if existing and existing.get("status") == COMPLETED and _assets_present(existing):
            log.info("job %s already completed (%d asset(s)); reusing",
                     job_id, len(existing["assets"]))
            existing["reused"] = True
            return existing

        job = existing or {
            "job_id": job_id,
            "request": request.to_dict(),
            "created_at": utc_now(),
            "attempts": [],
        }
        # A job found mid-flight (an interrupted run) is resumed, not restarted
        # from a clean slate - its attempt history stays intact.
        job.update({"status": RUNNING, "updated_at": utc_now(),
                    "out_dir": str(out_dir)})
        job.setdefault("attempts", [])
        save_job(job)

        state = _load_provider_state()
        candidates = self.candidates(request)
        if not candidates:
            job["status"] = FAILED
            job["error"] = ("no eligible provider: the request requires depicted "
                            "imagery and no configured provider can produce it"
                            if request.require_depicted else "no providers configured")
            job["updated_at"] = utc_now()
            save_job(job)
            raise GenerationError(job["error"], job["attempts"])

        for provider in candidates:
            cooling, remaining = _in_cooldown(state, provider.name)
            if cooling:
                log.info("skipping %s: in cooldown for another %.0fs",
                         provider.name, remaining)
                job["attempts"].append({
                    "provider": provider.name, "at": utc_now(),
                    "outcome": "skipped", "detail": f"cooldown {remaining:.0f}s left"})
                continue

            healthy, detail = provider.health(self.health_timeout)
            if not healthy:
                log.info("skipping %s: %s", provider.name, detail)
                job["attempts"].append({
                    "provider": provider.name, "at": utc_now(),
                    "outcome": "unhealthy", "detail": detail})
                # An unconfigured provider is not a failure, so it gets no
                # cooldown - there is nothing to recover from.
                if provider.configured():
                    _record_failure(state, provider.name, detail, self.cooldown_seconds)
                save_job(job)
                continue

            for attempt in range(1, self.attempts + 1):
                started = time.monotonic()
                job["status"] = RUNNING if attempt == 1 else RETRYING
                job["provider"] = provider.name
                job["updated_at"] = utc_now()
                save_job(job)
                log.info("generating via %s (attempt %d/%d)",
                         provider.name, attempt, self.attempts)
                try:
                    result = provider.generate(request, out_dir, self.generate_timeout)
                    # A provider that returns no usable asset has not succeeded,
                    # however calmly it said so. Treat it as a failure here so
                    # the router fails over instead of recording an empty job.
                    assets = [Path(a) for a in (result.get("assets") or [])]
                    missing = [a for a in assets if not a.is_file()]
                    if not assets:
                        raise GenerationError(
                            f"{provider.name} returned no assets")
                    if missing:
                        raise GenerationError(
                            f"{provider.name} reported assets that are not on disk: "
                            + ", ".join(str(a) for a in missing))
                    result["assets"] = assets
                except (GenerationError, OSError, ValueError) as e:
                    elapsed = time.monotonic() - started
                    log.warning("%s attempt %d failed after %.1fs: %s",
                                provider.name, attempt, elapsed, e)
                    job["attempts"].append({
                        "provider": provider.name, "at": utc_now(),
                        "outcome": "error", "attempt": attempt,
                        "elapsed_seconds": round(elapsed, 2), "detail": str(e)[:500]})
                    save_job(job)
                    if attempt >= self.attempts:
                        _record_failure(state, provider.name, str(e), self.cooldown_seconds)
                    continue

                elapsed = time.monotonic() - started
                _clear_failure(state, provider.name)
                job.update({
                    "status": COMPLETED,
                    "provider": provider.name,
                    "provider_job_id": result.get("provider_job_id"),
                    "model": result.get("model"),
                    "cost_usd": result.get("cost_usd"),
                    "notes": result.get("notes"),
                    "produces_depicted": provider.produces_depicted,
                    "assets": [str(p) for p in result["assets"]],
                    "elapsed_seconds": round(elapsed, 2),
                    "completed_at": utc_now(),
                    "updated_at": utc_now(),
                })
                job["attempts"].append({
                    "provider": provider.name, "at": utc_now(),
                    "outcome": "completed", "attempt": attempt,
                    "elapsed_seconds": round(elapsed, 2),
                    "assets": len(result["assets"])})
                save_job(job)
                log.info("%s produced %d asset(s) in %.1fs",
                         provider.name, len(result["assets"]), elapsed)
                return job

        job["status"] = FAILED
        reason = "every eligible provider failed or was unavailable"
        if request.require_depicted:
            excluded = [p.name for p in self.providers.values()
                        if not p.produces_depicted and p.name in self.order]
            if excluded:
                reason += (f"; {', '.join(excluded)} produce only abstract plates "
                           f"and were excluded because this request needs "
                           f"depicted imagery")
        job["error"] = reason
        job["updated_at"] = utc_now()
        save_job(job)
        raise GenerationError(job["error"], job["attempts"])


# --- CLI -------------------------------------------------------------------

def cmd_providers(args):
    router = Router()
    request = GenerationRequest(prompt="", require_depicted=args.depicted)
    log.info("routing order: %s", " -> ".join(router.order))
    if args.depicted:
        log.info("filtered to providers that produce depicted imagery")
    for entry in router.status(request):
        mark = "OK  " if entry["healthy"] else "DOWN"
        flags = []
        if not entry["configured"]:
            flags.append("unconfigured")
        if entry["costs_money"]:
            flags.append("COSTS MONEY")
        if not entry["produces_depicted"]:
            flags.append("abstract only")
        if entry["in_cooldown"]:
            flags.append(f"cooldown {entry['cooldown_remaining_seconds']:.0f}s")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{mark}  {entry['provider']:<12} {entry['detail']}{suffix}")
    return 0


def cmd_generate(args):
    request = GenerationRequest(
        prompt=args.prompt, negative_prompt=args.negative, width=args.width,
        height=args.height, count=args.count, seed=args.seed, model=args.model,
        style=args.style, require_depicted=args.depicted)
    router = Router()
    try:
        job = router.generate(request, Path(args.out))
    except GenerationError as e:
        log.error("generation failed: %s", e)
        for attempt in e.attempts:
            log.error("  %s: %s - %s", attempt["provider"],
                      attempt["outcome"], attempt.get("detail", ""))
        return 1
    print(json.dumps({k: job[k] for k in
                      ("job_id", "status", "provider", "model", "cost_usd", "assets")
                      if k in job}, indent=2))
    return 0


def cmd_jobs(args):
    if not JOBS_DIR.is_dir():
        print("no jobs recorded")
        return 0
    rows = []
    for path in sorted(JOBS_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            job = json.loads(path.read_text())
        except ValueError:
            continue
        rows.append(job)
    if not rows:
        print("no jobs recorded")
        return 0
    for job in sorted(rows, key=lambda j: j.get("updated_at", "")):
        print(f"{job.get('job_id'):<18} {job.get('status', '?'):<10} "
              f"{str(job.get('provider')):<12} {len(job.get('assets') or [])} asset(s)  "
              f"{job.get('updated_at', '')}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Provider-routed image generation.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prov = sub.add_parser("providers", help="show provider health and routing order")
    p_prov.add_argument("--depicted", action="store_true",
                        help="only providers that can produce depicted imagery")
    p_prov.set_defaults(func=cmd_providers)

    p_gen = sub.add_parser("generate", help="generate images through the router")
    p_gen.add_argument("--prompt", required=True)
    p_gen.add_argument("--negative", default=None)
    p_gen.add_argument("--out", required=True)
    p_gen.add_argument("--count", type=int, default=1)
    p_gen.add_argument("--width", type=int, default=1920)
    p_gen.add_argument("--height", type=int, default=1080)
    p_gen.add_argument("--seed", type=int, default=20260827)
    p_gen.add_argument("--model", default=None)
    p_gen.add_argument("--style", default="deep-night",
                       help="procedural fallback style")
    p_gen.add_argument("--depicted", action="store_true",
                       help="require depicted imagery; skip abstract-only providers")
    p_gen.set_defaults(func=cmd_generate)

    p_jobs = sub.add_parser("jobs", help="list recorded generation jobs")
    p_jobs.set_defaults(func=cmd_jobs)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Stock photo and footage discovery through official provider APIs.

Pexels, Pixabay and Unsplash, each behind one adapter that turns the vendor's
response into the same *candidate* shape (``scripts/visual_assets.py``) with
the provider's licence terms and the creator's credit attached. Nothing here
scrapes a website, rotates keys, or works around a limit: a provider that is
unconfigured, rate-limited or down is skipped for that search and reported,
and the other sources carry on.

Rules this module keeps:

* **Search is cheap, download is a decision.** ``search`` returns previews
  and metadata only. Bytes are fetched by ``download`` after a person has
  approved the candidate, and only from the provider's own CDN hosts.
* **Provider terms are respected, not circumvented.** Responses are cached
  for 24 hours (Pixabay requires it and it spares everyone's quota), rate
  limit headers are honoured and persisted across processes, a 429 puts the
  provider in a cooldown instead of being retried, and Unsplash's download
  tracking endpoint is called when a photo is actually downloaded.
* **Keys come from the environment and nowhere else** (``PEXELS_API_KEY``,
  ``PIXABAY_API_KEY``, ``UNSPLASH_ACCESS_KEY``). A missing key means the
  provider is "not configured", never a default and never an error.
* **A licence is the provider's published terms, not a verdict.** The
  adapter records what the provider says; ``asset_licensing`` decides what
  that is worth, and a person approves first use.

Standard library only. The HTTP layer is injectable so tests never touch the
network.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import asset_licensing
except ImportError:                                   # package-style import
    from . import asset_licensing

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STOCK_DIR = ROOT / "library" / "stock"

CACHE_TTL_SECONDS = 24 * 3600
DEFAULT_PER_PAGE = 15
MAX_PER_PAGE = 30
DEFAULT_COOLDOWN_SECONDS = 300.0
TRANSIENT_COOLDOWN_SECONDS = 60.0
AUTH_COOLDOWN_SECONDS = 3600.0
USER_AGENT = "ContentMachine/1.0 (creator media discovery)"
MAX_DOWNLOAD_BYTES = {"image": 100 * 1024 * 1024, "video": 400 * 1024 * 1024}

ORIENTATIONS = ("landscape", "portrait", "square")
KINDS = ("image", "video")


class StockError(Exception):
    """A provider could not serve this request. ``state`` says why."""

    state = "unavailable"

    def __init__(self, message, *, state=None, retry_after=None):
        super().__init__(message)
        if state:
            self.state = state
        self.retry_after = retry_after


class RateLimited(StockError):
    state = "rate_limited"


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        self.body = body


def default_transport(url, headers=None, timeout=20):
    """One GET. HTTP errors are returned, not raised: the status is data."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Response(response.status, dict(response.headers),
                            response.read(8 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers or {}), exc.read(64 * 1024))
    except (OSError, ValueError) as exc:
        raise StockError(f"network error: {exc}") from exc


def _orientation(width, height):
    if not width or not height:
        return None
    if abs(width - height) <= 0.03 * max(width, height):
        return "square"
    return "landscape" if width > height else "portrait"


def _scaled_height(width, original_width, original_height):
    if not (width and original_width and original_height):
        return None
    return round(width * original_height / original_width)


def _https(url):
    """A provider-supplied link, or None unless it is a plain https URL.

    These addresses end up in ``href`` and ``src`` attributes in the browser,
    so a ``javascript:`` or ``data:`` value from a compromised or confused
    response must never get that far.
    """
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except ValueError:
        return None
    if parsed.scheme == "https" and parsed.hostname and not parsed.username:
        return str(url)
    return None


def _tags(*values):
    seen, tags = set(), []
    for value in values:
        for part in re.split(r"[,;]", str(value or "")):
            part = part.strip().lower()
            if part and part not in seen:
                seen.add(part)
                tags.append(part)
    return tags


def _candidate(provider, kind, external_id, *, title, tags, width, height,
               duration, creator, creator_url, source_url, preview_url,
               full, variants=(), extra=None, retrieved_utc=None):
    """The provider-neutral candidate. Rights are attached by the caller's
    provider record (``asset_licensing.stock_rights``) so the terms live in
    one place."""
    creator_url, source_url, preview_url = _https(creator_url), _https(source_url), _https(preview_url)
    rights = asset_licensing.stock_rights(
        provider, creator=creator, creator_url=creator_url,
        source_url=source_url, kind=kind, external_id=external_id,
        retrieved_utc=retrieved_utc)
    return {
        "candidate_id": f"stock:{provider}:{kind}:{external_id}",
        "source": "stock",
        "provider": provider,
        "external_id": str(external_id),
        "media_kind": kind,
        "title": (title or "").strip(),
        "description": (title or "").strip(),
        "tags": tags,
        "width": width,
        "height": height,
        "orientation": _orientation(width, height),
        "duration_seconds": duration,
        "creator": {"name": creator, "url": creator_url},
        "source_url": source_url,
        "retrieved_utc": retrieved_utc,
        "preview": {"url": preview_url, "kind": "image"},
        "full": full,
        "variants": list(variants),
        "rights": rights,
        "cost": {"money_usd": 0.0, "resource": "network"},
        "state": "available",
        **(extra or {}),
    }


# --------------------------------------------------------------------------
# adapters
# --------------------------------------------------------------------------

class Provider:
    """One vendor. Subclasses build a request and parse a response; the hub
    owns caching, cooldowns, retries and deduplication."""

    name = ""
    key_env = ""
    kinds = KINDS
    hosts = ()                  # CDN hosts a download may come from
    label = ""

    # Why this provider is switched off even with a key, or None. A provider
    # whose terms have not been verified for this use stays off by default.
    enable_env = ""
    disabled_note = ""

    def key(self):
        return (os.environ.get(self.key_env) or "").strip()

    def disabled(self):
        if not self.enable_env:
            return None
        if (os.environ.get(self.enable_env) or "").strip().lower() in ("1", "true", "yes", "on"):
            return None
        return self.disabled_note

    def configured(self):
        return bool(self.key()) and self.disabled() is None

    def request(self, kind, query, *, orientation, min_width, min_height,
                page, per_page):
        raise NotImplementedError

    def parse(self, kind, payload, *, page, per_page, retrieved_utc):
        """``(candidates, more_pages)``."""
        raise NotImplementedError

    def rate_headers(self, headers):
        return {}

    def cache_identity(self, url):
        """The request URL without the credential, so a cache entry is the
        same whoever holds the key and the key never lands on disk."""
        return url

    def before_download(self, candidate, transport):
        return candidate["full"]["url"]


class Pexels(Provider):
    name = "pexels"
    label = "Pexels"
    key_env = "PEXELS_API_KEY"
    hosts = ("images.pexels.com", "videos.pexels.com")

    def request(self, kind, query, *, orientation, min_width, min_height,
                page, per_page):
        base = ("https://api.pexels.com/videos/search" if kind == "video"
                else "https://api.pexels.com/v1/search")
        params = {"query": query[:200], "page": page, "per_page": min(per_page, 80)}
        if orientation:
            params["orientation"] = orientation
        if kind == "image" and min_width and min_width >= 3000:
            params["size"] = "large"
        return (f"{base}?{urllib.parse.urlencode(params)}",
                {"Authorization": self.key()})

    def rate_headers(self, headers):
        return _standard_rate(headers, "x-ratelimit-")

    def parse(self, kind, payload, *, page, per_page, retrieved_utc):
        out = []
        if kind == "image":
            for item in payload.get("photos") or []:
                src = item.get("src") or {}
                width, height = item.get("width"), item.get("height")
                large = src.get("large2x") or src.get("large") or src.get("original")
                if not (large and item.get("id")):
                    continue
                full_width = min(width or 1880, 1880)
                out.append(_candidate(
                    self.name, "image", item["id"],
                    title=item.get("alt") or "", tags=_tags(item.get("alt")),
                    width=width, height=height, duration=None,
                    creator=item.get("photographer"),
                    creator_url=item.get("photographer_url"),
                    source_url=item.get("url"),
                    preview_url=src.get("medium") or src.get("small"),
                    full={"url": large, "width": full_width,
                          "height": _scaled_height(full_width, width, height),
                          "file_type": "image/jpeg"},
                    variants=[{"label": "original", "url": src["original"],
                               "width": width, "height": height}]
                    if src.get("original") else [],
                    retrieved_utc=retrieved_utc))
            more = bool(payload.get("next_page"))
        else:
            for item in payload.get("videos") or []:
                files = [f for f in item.get("video_files") or []
                         if (f.get("file_type") or "").startswith("video/") and f.get("link")]
                best = _pick_video_file(files)
                user = item.get("user") or {}
                if not (best and item.get("id")):
                    continue
                out.append(_candidate(
                    self.name, "video", item["id"],
                    title="", tags=_tags(item.get("url", "").rstrip("/").rsplit("/", 1)[-1].replace("-", ",")),
                    width=item.get("width"), height=item.get("height"),
                    duration=item.get("duration"),
                    creator=user.get("name"), creator_url=user.get("url"),
                    source_url=item.get("url"), preview_url=item.get("image"),
                    full={"url": best["link"], "width": best.get("width"),
                          "height": best.get("height"), "fps": best.get("fps"),
                          "file_type": best.get("file_type")},
                    variants=[{"label": f.get("quality"), "url": f["link"],
                               "width": f.get("width"), "height": f.get("height")}
                              for f in files if f is not best],
                    retrieved_utc=retrieved_utc))
            more = bool(payload.get("next_page"))
        return out, more


class Pixabay(Provider):
    name = "pixabay"
    label = "Pixabay"
    key_env = "PIXABAY_API_KEY"
    hosts = ("pixabay.com", "cdn.pixabay.com")

    def request(self, kind, query, *, orientation, min_width, min_height,
                page, per_page):
        base = ("https://pixabay.com/api/videos/" if kind == "video"
                else "https://pixabay.com/api/")
        params = {"key": self.key(), "q": query[:100], "safesearch": "true",
                  "page": page, "per_page": max(3, min(per_page, 200)),
                  "order": "popular"}
        if orientation in ("landscape", "portrait") and kind == "image":
            params["orientation"] = "horizontal" if orientation == "landscape" else "vertical"
        if min_width:
            params["min_width"] = int(min_width)
        if min_height:
            params["min_height"] = int(min_height)
        if kind == "image":
            params["image_type"] = "all"
        return f"{base}?{urllib.parse.urlencode(params)}", {}

    def cache_identity(self, url):
        return re.sub(r"([?&])key=[^&]*&?", r"\1", url).rstrip("?&")

    def rate_headers(self, headers):
        return _standard_rate(headers, "x-ratelimit-")

    def parse(self, kind, payload, *, page, per_page, retrieved_utc):
        out = []
        for item in payload.get("hits") or []:
            if not item.get("id"):
                continue
            user = item.get("user")
            creator_url = (f"https://pixabay.com/users/{user}-{item['user_id']}/"
                           if user and item.get("user_id") else None)
            if kind == "image":
                full_url = item.get("fullHDURL") or item.get("largeImageURL")
                if not full_url:
                    continue
                width, height = item.get("imageWidth"), item.get("imageHeight")
                full_width = 1920 if item.get("fullHDURL") else min(width or 1280, 1280)
                kind_label = item.get("type")
                out.append(_candidate(
                    self.name, "image", item["id"],
                    title=item.get("tags") or "", tags=_tags(item.get("tags"), kind_label),
                    width=width, height=height, duration=None,
                    creator=user, creator_url=creator_url,
                    source_url=item.get("pageURL"),
                    preview_url=item.get("webformatURL") or item.get("previewURL"),
                    full={"url": full_url, "width": full_width,
                          "height": _scaled_height(full_width, width, height),
                          "file_type": "image/jpeg"},
                    extra={"image_type": kind_label},
                    retrieved_utc=retrieved_utc))
            else:
                sizes = item.get("videos") or {}
                ranked = [sizes[k] for k in ("large", "medium", "small", "tiny")
                          if (sizes.get(k) or {}).get("url")]
                if not ranked:
                    continue
                best = ranked[0]
                out.append(_candidate(
                    self.name, "video", item["id"],
                    title=item.get("tags") or "", tags=_tags(item.get("tags"), item.get("type")),
                    width=best.get("width"), height=best.get("height"),
                    duration=item.get("duration"),
                    creator=user, creator_url=creator_url,
                    source_url=item.get("pageURL"),
                    preview_url=best.get("thumbnail"),
                    full={"url": best["url"], "width": best.get("width"),
                          "height": best.get("height"), "file_type": "video/mp4"},
                    variants=[{"label": k, "url": sizes[k]["url"],
                               "width": sizes[k].get("width"), "height": sizes[k].get("height")}
                              for k in ("medium", "small") if (sizes.get(k) or {}).get("url")],
                    retrieved_utc=retrieved_utc))
        total = payload.get("totalHits") or payload.get("total") or 0
        return out, page * per_page < total


class Unsplash(Provider):
    name = "unsplash"
    label = "Unsplash"
    key_env = "UNSPLASH_ACCESS_KEY"
    kinds = ("image",)
    hosts = ("images.unsplash.com",)
    # Optional and off until Unsplash's production API access (the demo tier
    # is rate-limited and not for production) and its usage requirements -
    # hotlinking, download tracking, attribution wording - have been checked
    # for this pipeline and a person sets UNSPLASH_ENABLED.
    enable_env = "UNSPLASH_ENABLED"
    disabled_note = ("Unsplash is off until its production API access and usage "
                     "requirements are verified")
    UTM = "utm_source=content_machine&utm_medium=referral"

    def request(self, kind, query, *, orientation, min_width, min_height,
                page, per_page):
        params = {"query": query[:200], "page": page,
                  "per_page": min(per_page, 30), "content_filter": "high"}
        if orientation:
            params["orientation"] = orientation
        return (f"https://api.unsplash.com/search/photos?{urllib.parse.urlencode(params)}",
                {"Authorization": f"Client-ID {self.key()}", "Accept-Version": "v1"})

    def rate_headers(self, headers):
        return _standard_rate(headers, "x-ratelimit-")

    def parse(self, kind, payload, *, page, per_page, retrieved_utc):
        out = []
        for item in payload.get("results") or []:
            urls, links = item.get("urls") or {}, item.get("links") or {}
            raw = urls.get("raw")
            if not (raw and item.get("id") and links.get("download_location")):
                continue
            user = item.get("user") or {}
            profile = ((user.get("links") or {}).get("html") or "")
            joiner = "&" if "?" in raw else "?"
            width, height = item.get("width"), item.get("height")
            full_width = min(width or 1920, 1920)
            text = " ".join(filter(None, [item.get("description"), item.get("alt_description")]))
            out.append(_candidate(
                self.name, "image", item["id"],
                title=text, tags=_tags(*[t.get("title") for t in item.get("tags") or []
                                         if isinstance(t, dict)], text),
                width=width, height=height, duration=None,
                creator=user.get("name"),
                creator_url=f"{profile}?{self.UTM}" if profile else None,
                source_url=f"{links.get('html')}?{self.UTM}" if links.get("html") else None,
                preview_url=urls.get("small") or urls.get("thumb"),
                full={"url": f"{raw}{joiner}w={full_width}&q=85&fm=jpg&fit=max",
                      "width": full_width,
                      "height": _scaled_height(full_width, width, height),
                      "file_type": "image/jpeg"},
                extra={"download_location": links["download_location"]},
                retrieved_utc=retrieved_utc))
        return out, page < int(payload.get("total_pages") or 0)

    def before_download(self, candidate, transport):
        """Unsplash's API terms: report a download when it happens."""
        location = candidate.get("download_location")
        if not location:
            raise StockError("Unsplash download tracking location is missing",
                             state="unavailable")
        response = transport(location, {"Authorization": f"Client-ID {self.key()}",
                                        "Accept-Version": "v1"})
        if response.status != 200:
            raise StockError(f"Unsplash download tracking failed ({response.status})")
        try:
            url = json.loads(response.body).get("url")
        except ValueError:
            url = None
        return url or candidate["full"]["url"]


def _standard_rate(headers, prefix):
    out = {}
    for field in ("limit", "remaining", "reset"):
        value = headers.get(prefix + field)
        try:
            out[field] = float(value)
        except (TypeError, ValueError):
            pass
    return out


def _pick_video_file(files, target_width=1920):
    """The smallest file that still covers the target width, else the largest."""
    usable = [f for f in files if f.get("width")]
    if not usable:
        return files[0] if files else None
    enough = sorted((f for f in usable if f["width"] >= target_width),
                    key=lambda f: f["width"])
    if enough:
        return enough[0]
    return max(usable, key=lambda f: f["width"])


PROVIDER_CLASSES = {"pexels": Pexels, "pixabay": Pixabay, "unsplash": Unsplash}
DEFAULT_ORDER = ("pexels", "pixabay", "unsplash")


# --------------------------------------------------------------------------
# the hub
# --------------------------------------------------------------------------

def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class StockHub:
    """Searches every configured provider, tolerating any of them failing."""

    def __init__(self, providers=None, *, directory=None, transport=None,
                 clock=time.time, sleep=time.sleep):
        if providers is None:
            order = [p.strip() for p in os.environ.get("STOCK_PROVIDERS", "").split(",")
                     if p.strip()] or list(DEFAULT_ORDER)
            providers = [PROVIDER_CLASSES[name]() for name in order
                         if name in PROVIDER_CLASSES]
        self.providers = list(providers)
        self.directory = Path(directory or os.environ.get("STOCK_DIR") or DEFAULT_STOCK_DIR)
        self.transport = transport or default_transport
        self.clock = clock
        self.sleep = sleep

    # -- persistence -------------------------------------------------------

    @property
    def _state_path(self):
        return self.directory / "state.json"

    def _state(self):
        try:
            data = json.loads(self._state_path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, state):
        self.directory.mkdir(parents=True, exist_ok=True)
        pending = self._state_path.with_suffix(".pending")
        pending.write_text(json.dumps(state, indent=2) + "\n")
        os.replace(pending, self._state_path)

    def _update_state(self, name, **fields):
        state = self._state()
        entry = state.setdefault(name, {})
        entry.update({k: v for k, v in fields.items() if v is not None})
        for key in [k for k, v in fields.items() if v is None]:
            entry.pop(key, None)
        self._save_state(state)

    def _cache_path(self, provider, url):
        digest = hashlib.sha256(provider.cache_identity(url).encode()).hexdigest()
        return self.directory / "cache" / f"{provider.name}-{digest[:32]}.json"

    def _cache_get(self, provider, url, *, allow_stale=False):
        path = self._cache_path(provider, url)
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        age = self.clock() - float(entry.get("fetched", 0))
        if age <= CACHE_TTL_SECONDS or allow_stale:
            return {**entry, "age": age, "stale": age > CACHE_TTL_SECONDS}
        return None

    def _cache_put(self, provider, url, payload, retrieved_utc):
        path = self._cache_path(provider, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(".pending")
        pending.write_text(json.dumps({"fetched": self.clock(), "retrieved_utc": retrieved_utc,
                                       "payload": payload}))
        os.replace(pending, path)

    # -- status --------------------------------------------------------------

    def provider_status(self, provider):
        now = self.clock()
        entry = self._state().get(provider.name, {})
        blocked = max(0.0, float(entry.get("blocked_until", 0)) - now)
        if provider.disabled() is not None:
            state, detail = "disabled", provider.disabled()
        elif not provider.configured():
            state, detail = "not_configured", f"{provider.key_env} is not set"
        elif blocked:
            state = entry.get("blocked_state") or "rate_limited"
            detail = (f"{entry.get('blocked_reason') or 'cooling down'}; "
                      f"retry in {int(blocked)}s")
        else:
            state, detail = "ready", "ready"
        return {"provider": provider.name, "label": provider.label, "state": state,
                "configured": provider.configured(), "detail": detail,
                "kinds": list(provider.kinds), "cooldown_seconds": round(blocked),
                "rate": entry.get("rate") or {}}

    def status(self):
        return [self.provider_status(p) for p in self.providers]

    # -- fetching ------------------------------------------------------------

    def _block(self, provider, state, reason, seconds):
        self._update_state(provider.name, blocked_until=self.clock() + seconds,
                           blocked_state=state, blocked_reason=reason)

    def _fetch(self, provider, url, headers):
        """One cached, rate-aware, once-retried GET returning parsed JSON."""
        cached = self._cache_get(provider, url)
        if cached:
            return cached["payload"], cached["retrieved_utc"], "cache"
        status = self.provider_status(provider)
        if status["state"] != "ready":
            stale = self._cache_get(provider, url, allow_stale=True)
            if stale and status["state"] == "rate_limited":
                return stale["payload"], stale["retrieved_utc"], "stale-cache"
            raise StockError(status["detail"], state=status["state"])
        response = None
        for attempt in (0, 1):
            try:
                response = self.transport(url, headers)
            except StockError:
                response = None
            if response is not None and response.status < 500:
                break
            if attempt == 0:
                self.sleep(0.5)
        if response is None or response.status >= 500:
            self._block(provider, "unavailable", "provider is not responding",
                        TRANSIENT_COOLDOWN_SECONDS)
            raise StockError(f"{provider.label} is not responding", state="unavailable")

        rate = provider.rate_headers(response.headers)
        if rate:
            self._update_state(provider.name, rate=rate)
        if response.status == 429 or (rate.get("remaining") == 0 and response.status == 200):
            wait = _retry_after(response.headers, rate, self.clock())
            self._block(provider, "rate_limited", "rate limit reached", wait)
            if response.status == 429:
                stale = self._cache_get(provider, url, allow_stale=True)
                if stale:
                    return stale["payload"], stale["retrieved_utc"], "stale-cache"
                raise RateLimited(f"{provider.label} rate limit reached",
                                  retry_after=wait)
        if response.status in (401, 403):
            self._block(provider, "auth_failed",
                        f"{provider.key_env} was rejected", AUTH_COOLDOWN_SECONDS)
            raise StockError(f"{provider.label} rejected the API key", state="auth_failed")
        if response.status != 200:
            raise StockError(f"{provider.label} answered {response.status}",
                             state="unavailable")
        try:
            payload = json.loads(response.body)
        except ValueError as exc:
            raise StockError(f"{provider.label} returned unreadable data",
                             state="unavailable") from exc
        retrieved = utc_now()
        self._cache_put(provider, url, payload, retrieved)
        return payload, retrieved, "network"

    def search(self, query, *, kind="image", orientation="landscape",
               min_width=None, min_height=None, pages=None, per_page=DEFAULT_PER_PAGE,
               only=None):
        """Candidates from every capable, configured provider.

        ``pages`` is ``{provider: page}`` for the next page of a previous
        search; absent means page one. Returns ``{"candidates", "providers",
        "next_pages"}``. A failing provider appears in ``providers`` with its
        state and never raises: the point is that one outage does not stop
        the others.
        """
        if kind not in KINDS:
            raise StockError(f"unsupported media kind: {kind}", state="unsupported")
        query = (query or "").strip()
        if not query:
            raise StockError("a search needs words describing the picture", state="invalid")
        per_page = max(3, min(int(per_page), MAX_PER_PAGE))
        report, found, next_pages = [], [], {}
        for provider in self.providers:
            if only and provider.name not in only:
                continue
            entry = {"provider": provider.name, "label": provider.label,
                     "state": "ready", "detail": "", "count": 0, "from": None}
            report.append(entry)
            if kind not in provider.kinds:
                entry.update(state="unsupported",
                             detail=f"{provider.label} has no {kind} search")
                continue
            if provider.disabled() is not None:
                entry.update(state="disabled", detail=provider.disabled())
                continue
            if not provider.configured():
                entry.update(state="not_configured",
                             detail=f"{provider.key_env} is not set")
                continue
            page = int((pages or {}).get(provider.name, 1))
            url, headers = provider.request(
                kind, query, orientation=orientation, min_width=min_width,
                min_height=min_height, page=page, per_page=per_page)
            try:
                payload, retrieved, origin = self._fetch(provider, url, headers)
                items, more = provider.parse(kind, payload, page=page, per_page=per_page,
                                             retrieved_utc=retrieved)
            except StockError as exc:
                entry.update(state=exc.state, detail=str(exc))
                continue
            entry.update(count=len(items), **{"from": origin})
            if origin == "stale-cache":
                entry["detail"] = "rate limited; showing earlier results"
            if more:
                next_pages[provider.name] = page + 1
            found.extend(items)
        return {"candidates": dedupe(found), "providers": report,
                "next_pages": next_pages}

    # -- download --------------------------------------------------------------

    def provider(self, name):
        for provider in self.providers:
            if provider.name == name:
                return provider
        raise StockError(f"unknown stock provider: {name}", state="unsupported")

    def download(self, candidate, directory):
        """Fetch a candidate's full-size bytes into ``directory``.

        Only from the provider's own hosts, over https, following no redirect
        that leaves them, with a size cap. Returns the file's path.
        """
        provider = self.provider(candidate["provider"])
        if provider.disabled() is not None:
            raise StockError(provider.disabled(), state="disabled")
        if not provider.configured():
            raise StockError(f"{provider.key_env} is not set", state="not_configured")
        status = self.provider_status(provider)
        if status["state"] in ("rate_limited", "auth_failed"):
            raise StockError(status["detail"], state=status["state"])
        url = provider.before_download(candidate, self.transport)
        _safe_host(url, provider.hosts)
        kind = candidate["media_kind"]
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".webm"}:
            suffix = ".mp4" if kind == "video" else ".jpg"
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(candidate["external_id"]))[:64]
        target = directory / f"{provider.name}-{safe_id}{suffix}"
        pending = target.with_suffix(target.suffix + ".pending")
        limit = MAX_DOWNLOAD_BYTES[kind]
        opener = urllib.request.build_opener(_HostRedirect(provider.hosts))
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                             timeout=60) as response, pending.open("wb") as out:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise StockError(f"download exceeds the {limit // 2**20} MB limit")
                    out.write(chunk)
        except StockError:
            pending.unlink(missing_ok=True)
            raise
        except (OSError, ValueError) as exc:
            pending.unlink(missing_ok=True)
            raise StockError(f"download interrupted: {exc}") from exc
        os.replace(pending, target)
        return target


def _retry_after(headers, rate, now):
    try:
        return max(1.0, float(headers.get("retry-after")))
    except (TypeError, ValueError):
        pass
    reset = rate.get("reset")
    if reset:
        # Providers send either seconds-until-reset or an epoch timestamp.
        wait = reset - now if reset > 10_000_000 else reset
        if wait > 0:
            return min(wait, 24 * 3600.0)
    return DEFAULT_COOLDOWN_SECONDS


def _safe_host(url, hosts):
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        ok = (parsed.scheme == "https" and not parsed.username
              and parsed.port in (None, 443)
              and any(host == h or host.endswith("." + h) for h in hosts))
    except (ValueError, TypeError, AttributeError):
        ok = False
    if not ok:
        raise StockError("refusing to download from a host the provider does not own",
                         state="unsafe")


class _HostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _safe_host(newurl, self.hosts)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def dedupe(candidates):
    """Drop repeats within and across providers, keeping the first seen.

    Within a provider the id is identity. Across providers the same photo is
    sometimes mirrored, so a candidate by the same creator with the same
    pixel dimensions (and duration, for footage) is treated as one.
    """
    seen_ids, seen_keys, out = set(), set(), []
    for item in candidates:
        if item["candidate_id"] in seen_ids:
            continue
        seen_ids.add(item["candidate_id"])
        creator = ((item.get("creator") or {}).get("name") or "").strip().lower()
        key = (creator, item.get("width"), item.get("height"),
               item.get("duration_seconds"), item["media_kind"])
        if creator and item.get("width") and key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(item)
    return out

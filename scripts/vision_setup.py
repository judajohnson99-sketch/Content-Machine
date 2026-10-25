#!/usr/bin/env python3
"""Get a GPU worker ready to judge images with a local vision model.

Everything the PC needs, as small commands that report what they find and
change nothing unless asked:

    python3 scripts/vision_setup.py doctor          # what is ready, what is missing, what to do
    python3 scripts/vision_setup.py env [--write]   # the .env lines (--write adds the missing ones)
    python3 scripts/vision_setup.py pull            # download the configured models into Ollama
    python3 scripts/vision_setup.py smoke           # judge two test images end to end
    python3 scripts/vision_setup.py service         # systemd units to keep the worker running

It does not install Ollama (get it from https://ollama.com/download, which
sets up its own service), does not download anything except through
``pull``, and never contacts a paid service: Ollama runs the model on this
machine. Standard library only; the worker already requires Linux.

The order on a fresh machine is ``doctor`` -> install Ollama -> ``env
--write`` -> ``pull`` -> ``doctor`` -> ``smoke`` -> run the calibration
(``vision_calibration.py run --model ... --apply``) -> start the worker.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envfile  # noqa: E402
import gpu_coordination as gpu  # noqa: E402
import vision  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Where Ollama listens when installed normally. Used only to suggest a value
# and to look for a local Ollama in `doctor`: the pipeline itself never
# defaults to it (an unset QUALITY_VISION_URL means "no vision model here").
OLLAMA_LOCAL = "http://127.0.0.1:11434"

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# What `env` proposes. A value already in .env is never replaced.
ENV_BLOCK = (
    ("QUALITY_VISION_URL", OLLAMA_LOCAL,
     "this machine's own Ollama; unset means this worker will not judge images"),
    ("QUALITY_VISION_MODELS", ",".join(vision.DEFAULT_MODELS),
     "tried in order; the first one that is installed and behaving judges"),
    ("WORKER_VISION", "1", "judge each rendered image before reporting it"),
    ("QUALITY_VISION_GPU_COORDINATION", "auto",
     "auto: take the GPU lease and keep clear of ComfyUI on this host"),
    ("QUALITY_VISION_KEEP_ALIVE", "0", "unload the model after every image so ComfyUI gets the VRAM back"),
    ("QUALITY_VISION_TIMEOUT", "180", "seconds per image; the first one includes loading the model"),
    ("QUALITY_VISION_BUDGET_SECONDS", "300", "seconds per job spent judging, at most"),
    ("QUALITY_VISION_FREE_SETTLE_SECONDS", "2", "seconds to let ComfyUI release VRAM before the model loads"),
)


# Qwen3-VL needs Ollama 0.12.7 or newer (the model's library page says so).
MIN_OLLAMA = {"qwen3-vl": (0, 12, 7)}


def chain_for_version(models):
    return list(models) if models else vision.configured_models()


def _too_old_for(version_text, chain):
    """A warning when the installed Ollama predates a configured model, else None."""
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", version_text or "")
    if not found:
        return None
    have = tuple(int(n) for n in found.groups())
    for model in chain:
        needed = MIN_OLLAMA.get(model.split(":")[0])
        if needed and have < needed:
            return (f"Ollama {'.'.join(map(str, have))} is older than "
                    f"{'.'.join(map(str, needed))}, which {model} needs")
    return None


def check(name, status, detail, hint=None):
    return {"name": name, "status": status, "detail": detail, "hint": hint}


def _run(cmd, timeout=10):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.returncode, out.stdout.strip(), out.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def doctor(url=None, models=None):
    """Every check, as a list; nothing is changed and nothing is downloaded."""
    results = []
    results.append(check(
        "platform", PASS if sys.platform.startswith("linux") else FAIL,
        f"{sys.platform}, Python {sys.version.split()[0]}",
        None if sys.platform.startswith("linux") else
        "the worker uses fcntl and needs Linux (or WSL)"))
    results.append(check(
        "ffmpeg", PASS if shutil.which("ffmpeg") else FAIL,
        shutil.which("ffmpeg") or "not found",
        None if shutil.which("ffmpeg") else "install ffmpeg: images are shrunk with it before judging"))

    smi = shutil.which("nvidia-smi")
    if smi:
        code, out, _ = _run([smi, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader"])
        results.append(check("gpu", PASS if code == 0 else WARN, out or "nvidia-smi gave no answer"))
    else:
        results.append(check("gpu", WARN, "nvidia-smi not found",
                             "without an NVIDIA GPU Ollama runs on the CPU, which works but is slow"))

    ollama_bin = shutil.which("ollama")
    if ollama_bin:
        _, version, _ = _run([ollama_bin, "--version"])
        results.append(check("ollama binary", PASS, f"{ollama_bin} ({version})"))
        too_old = _too_old_for(version, chain_for_version(models))
        if too_old:
            results.append(check("ollama version", WARN, too_old,
                                 "update Ollama from https://ollama.com/download"))
    else:
        results.append(check("ollama binary", WARN, "not on PATH",
                             "install it from https://ollama.com/download (or point QUALITY_VISION_URL "
                             "at an Ollama that runs elsewhere)"))

    configured = (url or os.environ.get("QUALITY_VISION_URL") or "").rstrip("/")
    if not configured:
        results.append(check(
            "QUALITY_VISION_URL", WARN, "not set - this worker will not judge images",
            "run: python3 scripts/vision_setup.py env --write"))
    probe_url = configured or OLLAMA_LOCAL
    chain = list(models) if models else vision.configured_models()
    provider = vision.OllamaProvider(url=probe_url)
    installed = None
    try:
        installed = provider.installed(timeout=5)
        results.append(check("ollama server", PASS, f"answering at {probe_url}"
                             + ("" if configured else " (not configured; found by looking locally)")))
    except vision.VisionError as e:
        results.append(check("ollama server", WARN if not configured else FAIL, e.detail,
                             "start it with `ollama serve` (the installer usually runs it as a service)"))
    if installed is not None:
        ready = [m for m in chain if vision.installed_match(m, installed)]
        missing = [m for m in chain if m not in ready]
        if ready:
            results.append(check("vision models", PASS if not missing else WARN,
                                 f"installed: {', '.join(ready)}"
                                 + (f"; not installed: {', '.join(missing)}" if missing else ""),
                                 f"python3 scripts/vision_setup.py pull --model {missing[0]}" if missing else None))
        else:
            results.append(check("vision models", FAIL, f"none of {', '.join(chain)} is installed",
                                 "python3 scripts/vision_setup.py pull"))
        for model in ready:
            done = vision.thresholds_for(model).get("calibrated_dimensions") or []
            results.append(check(
                f"calibration: {model}", PASS if len(done) == len(vision.DIMENSIONS) else WARN,
                "calibrated for " + (", ".join(done) if done else "nothing yet"),
                None if len(done) == len(vision.DIMENSIONS) else
                f"python3 scripts/vision_calibration.py run --model {model} --apply"))

    comfy = (os.environ.get("COMFYUI_URL") or "").rstrip("/")
    if comfy:
        busy, detail = gpu.comfyui_busy(comfy)
        reachable = "not reachable" not in detail
        results.append(check("comfyui", PASS if reachable else WARN,
                             f"{comfy}: {detail}",
                             None if reachable else "start ComfyUI; the worker needs it for renders"))
    else:
        results.append(check("comfyui", WARN, "COMFYUI_URL is not set",
                             "the worker agent refuses to start without it"))

    lock = gpu.lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a"):
            pass
        results.append(check("gpu lease file", PASS, str(lock)))
    except OSError as e:
        results.append(check("gpu lease file", FAIL, f"{lock}: {e}", "set GPU_LOCK_PATH to a writable path"))

    results.append(check(
        "worker vision flag", PASS if os.environ.get("WORKER_VISION") else WARN,
        f"WORKER_VISION={os.environ.get('WORKER_VISION') or '(unset)'}",
        None if os.environ.get("WORKER_VISION") else "set WORKER_VISION=1 so the agent judges what it renders"))
    missing_cp = [k for k in ("CONTROL_PLANE_URL", "WORKER_TOKEN") if not os.environ.get(k)]
    results.append(check(
        "control plane", PASS if not missing_cp else WARN,
        "CONTROL_PLANE_URL and WORKER_TOKEN are set" if not missing_cp else f"{', '.join(missing_cp)} not set",
        None if not missing_cp else "see WORKER_RUNBOOK.md, step 4"))

    try:
        import vision_calibration as cal
        problems = cal.verify()
        results.append(check("calibration dataset", PASS if not problems else FAIL,
                             f"{len(cal.load_cases())} labelled images, hashes match" if not problems
                             else f"{len(problems)} problem(s): {problems[0]}",
                             None if not problems else "git checkout calibration/vision"))
    except (OSError, ValueError) as e:
        results.append(check("calibration dataset", FAIL, str(e)))
    return results


def print_doctor(results):
    width = max(len(r["name"]) for r in results)
    for r in results:
        print(f"{r['status']:<5} {r['name']:<{width}}  {r['detail']}")
        if r["hint"] and r["status"] != PASS:
            print(f"{'':<5} {'':<{width}}  -> {r['hint']}")
    failed = [r for r in results if r["status"] == FAIL]
    warned = [r for r in results if r["status"] == WARN]
    print(f"\n{len(failed)} to fix, {len(warned)} to look at, "
          f"{sum(1 for r in results if r['status'] == PASS)} fine.")
    return 1 if failed else 0


# --------------------------------------------------------------------------
# env
# --------------------------------------------------------------------------

def env_lines(existing):
    """The lines `env --write` would add, given what .env already defines."""
    return [(k, v, why) for k, v, why in ENV_BLOCK if k not in existing]


def write_env(path=None):
    """Append the missing keys to ``.env``. Never overwrites or reorders."""
    path = Path(path or envfile.DEFAULT_PATH)
    existing = envfile.parse_env_file(path)
    missing = env_lines(existing)
    if missing:
        text = path.read_text() if path.is_file() else ""
        with path.open("a") as stream:
            if text and not text.endswith("\n"):
                stream.write("\n")
            stream.write("\n# --- Vision model on this worker (scripts/vision_setup.py env --write) ---\n")
            for key, value, why in missing:
                stream.write(f"# {why}\n{key}={value}\n")
    return [k for k, _, _ in missing], [k for k, _, _ in ENV_BLOCK if k in existing]


# --------------------------------------------------------------------------
# pull
# --------------------------------------------------------------------------

def pull_model(url, model, out=print, read_timeout=900):
    """Ask Ollama to download ``model``, reporting progress. Returns ``(ok, detail)``.

    The download comes from Ollama's own registry through the Ollama server on
    this machine; this script only asks for it and reports what Ollama says,
    including an unknown model name, which Ollama (not this script) rejects.
    """
    body = json.dumps({"model": model, "stream": True}).encode("utf-8")
    req = urllib.request.Request(f"{url.rstrip('/')}/api/pull", data=body,
                                 headers={"Content-Type": "application/json"})
    last_pct, last_status = -10, None
    try:
        with urllib.request.urlopen(req, timeout=read_timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("error"):
                    return False, str(event["error"])
                status = event.get("status") or ""
                total, done = event.get("total"), event.get("completed")
                if total and done is not None:
                    pct = int(100 * done / total)
                    if pct >= last_pct + 10 or pct == 100:
                        out(f"  {model}: {status} {pct}%")
                        last_pct = pct
                elif status and status != last_status:
                    out(f"  {model}: {status}")
                last_status = status
                if status == "success":
                    return True, "downloaded"
    except urllib.error.HTTPError as e:
        return False, f"Ollama answered HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"Ollama is not reachable at {url}: {getattr(e, 'reason', e)}"
    return False, "the download ended without Ollama reporting success"


# --------------------------------------------------------------------------
# smoke
# --------------------------------------------------------------------------

def smoke(coordination=None, runner=None):
    """Judge a matching and a mismatched image through the real path.

    A sanity check, not a calibration: the model has to answer in the
    required format, and the matching image has to outscore the one that
    shows the wrong thing. Returns ``(exit_code, lines)``.
    """
    import vision_calibration as cal
    lines = []
    with tempfile.TemporaryDirectory(prefix="cm-vision-smoke-") as tmp:
        tmp = Path(tmp)
        shapes = [cal.shape("circle", "red", "left", "large")]
        intent = cal.describe(shapes)
        cal.render("night", shapes, tmp / "match.jpg")
        cal.render("night", cal._mismatch(shapes, "colour"), tmp / "mismatch.jpg")
        if runner is None:
            kwargs = {"cache": vision.Cache(tmp / "cache")}
            if coordination:
                kwargs["coordination"] = coordination
            runner = vision.Runner(**kwargs)
        health = runner.availability()
        if not health["ready"]:
            return 2, [f"not ready: {health['detail']}"]
        lines.append(f"model: {health['models'][0]}  (intent: {intent!r})")
        started = time.monotonic()
        result = runner.judge_images([
            {"key": "match", "path": tmp / "match.jpg", "intent": intent},
            {"key": "mismatch", "path": tmp / "mismatch.jpg", "intent": intent}])
        elapsed = time.monotonic() - started
    for key in ("match", "mismatch"):
        if key in result.judgements:
            j = result.judgements[key]
            lines.append(f"{key:<9} adherence {j['adherence']['score']}/5, composition "
                         f"{j['composition']['score']}/5, artifacts {j['artifacts']['found'] or 'none'} "
                         f"({j['seconds']}s)")
        else:
            lines.append(f"{key:<9} not judged: {result.skipped[key]['detail']}")
    lines.append(f"total {elapsed:.1f}s")
    if len(result.judgements) < 2:
        return 2, lines + ["smoke test incomplete: see the reasons above"]
    good, bad = result.judgements["match"], result.judgements["mismatch"]
    if good["adherence"]["score"] > bad["adherence"]["score"]:
        return 0, lines + ["smoke OK: the matching image outscored the mismatched one "
                           "(this is a sanity check, not a calibration)"]
    return 1, lines + ["smoke FAILED: the mismatched image did not score lower - this model cannot "
                       "tell them apart; try another in QUALITY_VISION_MODELS"]


# --------------------------------------------------------------------------
# service
# --------------------------------------------------------------------------

WORKER_UNIT = """[Unit]
Description=Content Machine GPU worker agent
After=network-online.target

[Service]
Type=simple
WorkingDirectory={root}
EnvironmentFile={root}/.env
ExecStart={python} scripts/worker_agent.py
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
"""

TUNNEL_UNIT = """[Unit]
Description=Content Machine SSH tunnel to the control plane (outbound only)
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ssh -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -L 8788:127.0.0.1:8788 {vps}
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
"""


def service_units(vps=None, python=None, root=None):
    root = str(Path(root or ROOT))
    units = {"content-machine-worker.service": WORKER_UNIT.format(
        root=root, python=python or sys.executable or "/usr/bin/python3")}
    if vps:
        units["content-machine-tunnel.service"] = TUNNEL_UNIT.format(vps=vps)
    return units


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    envfile.load_env_file()
    parser = argparse.ArgumentParser(description="Set up a GPU worker to judge images with a local vision model.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("doctor", help="check what is ready and what is missing")
    p.add_argument("--url", default=None)
    p.set_defaults(func=lambda a: print_doctor(doctor(a.url)))
    p = sub.add_parser("env", help="show (or --write) the .env lines for vision")
    p.add_argument("--write", action="store_true", help="append the missing lines to .env")
    p.set_defaults(func=_cmd_env)
    p = sub.add_parser("pull", help="download the configured models into Ollama")
    p.add_argument("--model", action="append", default=None)
    p.add_argument("--url", default=None)
    p.set_defaults(func=_cmd_pull)
    p = sub.add_parser("smoke", help="judge two test images end to end")
    p.add_argument("--coordination", choices=("auto", "on", "off"), default=None)
    p.set_defaults(func=_cmd_smoke)
    p = sub.add_parser("service", help="systemd units that keep the worker (and tunnel) running")
    p.add_argument("--vps", default=None, help="user@host of the control plane, to also emit the tunnel unit")
    p.add_argument("--write", default=None, metavar="DIR",
                   help="write the units into DIR (for example ~/.config/systemd/user)")
    p.set_defaults(func=_cmd_service)
    args = parser.parse_args(argv)
    return args.func(args)


def _cmd_env(args):
    if args.write:
        added, kept = write_env()
        print(f"added to .env: {', '.join(added) or 'nothing (all present)'}")
        if kept:
            print(f"left as they were: {', '.join(kept)}")
        return 0
    existing = envfile.parse_env_file(envfile.DEFAULT_PATH)
    for key, value, why in ENV_BLOCK:
        mark = "(already set)" if key in existing else ""
        print(f"# {why} {mark}\n{key}={value}")
    return 0


def _cmd_pull(args):
    url = (args.url or os.environ.get("QUALITY_VISION_URL") or "").rstrip("/")
    if not url:
        print("QUALITY_VISION_URL is not set; pass --url or run `env --write` first")
        return 2
    failed = 0
    for model in (args.model or vision.configured_models()):
        print(f"pulling {model} ...")
        ok, detail = pull_model(url, model)
        print(f"  {'done' if ok else 'FAILED'}: {detail}")
        failed += 0 if ok else 1
    return 1 if failed else 0


def _cmd_smoke(args):
    code, lines = smoke(args.coordination)
    print("\n".join(lines))
    return code


def _cmd_service(args):
    units = service_units(args.vps)
    if args.write:
        target = Path(args.write).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        for name, text in units.items():
            (target / name).write_text(text)
            print(f"wrote {target / name}")
        print("then: systemctl --user daemon-reload && systemctl --user enable --now "
              + " ".join(units))
        print("keep it running after logout: loginctl enable-linger $USER")
        return 0
    for name, text in units.items():
        print(f"# --- {name}\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

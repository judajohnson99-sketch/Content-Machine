#!/usr/bin/env python3
"""Tests for the generation router, providers, and job persistence.

Covers the autonomy-critical path: which provider gets picked, what happens
when one is down, slow, or lying, and whether an interrupted run resumes
without generating the same assets twice.

No test needs a GPU, a workstation, or a paid API. The ComfyUI adapter is
exercised against a real local HTTP server so the actual submit/poll/download
protocol is tested, not a mock of it, and the Gemini adapter the same way.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import generation  # noqa: E402


class FakeProvider(generation.Provider):
    """Scriptable provider: decides health, failures and output per call."""

    def __init__(self, name, healthy=True, produces_depicted=True,
                 fail_times=0, error=None, configured=True, delay=0.0,
                 costs_money=False):
        self.name = name
        self.produces_depicted = produces_depicted
        self.costs_money = costs_money
        self._healthy = healthy
        self._configured = configured
        self.fail_times = fail_times
        self.error = error or generation.GenerationError(f"{name} exploded")
        self.delay = delay
        self.calls = 0
        self.health_calls = 0

    def configured(self):
        return self._configured

    def health(self, timeout=None):
        self.health_calls += 1
        return (self._healthy, f"{self.name}: {'up' if self._healthy else 'down'}")

    def generate(self, request, out_dir, timeout=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.calls <= self.fail_times:
            raise self.error
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        assets = []
        for i in range(request.count):
            path = out_dir / f"{self.name}_{i + 1}.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + self.name.encode())
            assets.append(path)
        return {"assets": assets, "provider_job_id": f"{self.name}-job",
                "model": f"{self.name}-model", "cost_usd": 0.0}


class RouterTestCase(unittest.TestCase):
    """Router behaviour with fully controlled providers."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-gen-"))
        # Redirect all job/cooldown state so tests never touch the real jobs/.
        self._orig_jobs = generation.JOBS_DIR
        self._orig_state = generation.PROVIDER_STATE_PATH
        generation.JOBS_DIR = self.tmp / "jobs"
        generation.PROVIDER_STATE_PATH = generation.JOBS_DIR / "_provider_state.json"
        self.out = self.tmp / "out"

    def tearDown(self):
        generation.JOBS_DIR = self._orig_jobs
        generation.PROVIDER_STATE_PATH = self._orig_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def router(self, providers, **kw):
        kw.setdefault("order", [p.name for p in providers])
        kw.setdefault("cooldown_seconds", 300.0)
        return generation.Router(providers={p.name: p for p in providers}, **kw)

    def request(self, **kw):
        kw.setdefault("prompt", "a quiet room")
        return generation.GenerationRequest(**kw)

    # --- provider selection ---

    def test_prefers_first_healthy_provider(self):
        comfy = FakeProvider("comfyui")
        proc = FakeProvider("procedural", produces_depicted=False)
        job = self.router([comfy, proc]).generate(self.request(), self.out)
        self.assertEqual(job["provider"], "comfyui")
        self.assertEqual(job["status"], generation.COMPLETED)
        self.assertEqual(proc.calls, 0, "fallback ran despite a healthy preferred provider")

    def test_falls_back_when_comfyui_is_offline(self):
        comfy = FakeProvider("comfyui", healthy=False)
        proc = FakeProvider("procedural", produces_depicted=False)
        job = self.router([comfy, proc]).generate(self.request(), self.out)
        self.assertEqual(job["provider"], "procedural")
        self.assertEqual(comfy.calls, 0, "an unhealthy provider was still called")
        self.assertTrue(Path(job["assets"][0]).is_file())

    def test_falls_through_to_external_api_when_local_options_fail(self):
        comfy = FakeProvider("comfyui", healthy=False)
        proc = FakeProvider("procedural", healthy=False, produces_depicted=False)
        api = FakeProvider("api", costs_money=True)
        job = self.router([comfy, proc, api]).generate(self.request(), self.out)
        self.assertEqual(job["provider"], "api")

    def test_unconfigured_provider_is_skipped_without_cooldown(self):
        """Not configured is not the same as broken - nothing to recover from."""
        api = FakeProvider("api", healthy=False, configured=False)
        proc = FakeProvider("procedural", produces_depicted=False)
        self.router([api, proc]).generate(self.request(), self.out)
        state = generation._load_provider_state()
        self.assertNotIn("api", state)

    # --- depicted-imagery routing ---

    def test_require_depicted_excludes_abstract_only_providers(self):
        comfy = FakeProvider("comfyui", healthy=False)
        proc = FakeProvider("procedural", produces_depicted=False)
        router = self.router([comfy, proc])
        with self.assertRaises(generation.GenerationError) as ctx:
            router.generate(self.request(require_depicted=True), self.out)
        self.assertIn("depicted", str(ctx.exception))
        self.assertEqual(proc.calls, 0,
                         "abstract provider was used for a depicted-imagery request")

    def test_require_depicted_still_uses_a_capable_provider(self):
        comfy = FakeProvider("comfyui")
        proc = FakeProvider("procedural", produces_depicted=False)
        job = self.router([comfy, proc]).generate(
            self.request(require_depicted=True), self.out)
        self.assertEqual(job["provider"], "comfyui")

    # --- retries, failover, cooldown ---

    def test_retries_then_succeeds_on_the_same_provider(self):
        comfy = FakeProvider("comfyui", fail_times=1)
        job = self.router([comfy], attempts=2).generate(self.request(), self.out)
        self.assertEqual(job["provider"], "comfyui")
        self.assertEqual(comfy.calls, 2)
        outcomes = [a["outcome"] for a in job["attempts"]]
        self.assertEqual(outcomes, ["error", "completed"])

    def test_exhausted_retries_fail_over_to_the_next_provider(self):
        comfy = FakeProvider("comfyui", fail_times=99)
        proc = FakeProvider("procedural", produces_depicted=False)
        job = self.router([comfy, proc], attempts=2).generate(self.request(), self.out)
        self.assertEqual(job["provider"], "procedural")
        self.assertEqual(comfy.calls, 2, "retry budget was not respected")

    def test_all_providers_failing_raises_rather_than_hanging(self):
        a = FakeProvider("comfyui", fail_times=99)
        b = FakeProvider("procedural", fail_times=99, produces_depicted=False)
        with self.assertRaises(generation.GenerationError):
            self.router([a, b], attempts=1).generate(self.request(), self.out)
        job = generation.load_job(self.request().digest())
        self.assertEqual(job["status"], generation.FAILED)

    def test_failed_provider_enters_cooldown_and_is_skipped(self):
        comfy = FakeProvider("comfyui", fail_times=99)
        proc = FakeProvider("procedural", produces_depicted=False)
        router = self.router([comfy, proc], attempts=1)
        router.generate(self.request(), self.out)
        self.assertEqual(comfy.calls, 1)

        # A different request must not re-hammer the provider we just saw die.
        router.generate(self.request(prompt="another"), self.out)
        self.assertEqual(comfy.calls, 1, "provider in cooldown was retried")
        skipped = [a for a in generation.load_job(self.request(prompt="another").digest())["attempts"]
                   if a["outcome"] == "skipped"]
        self.assertTrue(skipped)

    def test_cooldown_expires_and_the_provider_is_tried_again(self):
        comfy = FakeProvider("comfyui", fail_times=1)
        proc = FakeProvider("procedural", produces_depicted=False)
        router = self.router([comfy, proc], attempts=1, cooldown_seconds=0.0)
        router.generate(self.request(), self.out)
        self.assertEqual(comfy.calls, 1)
        router.generate(self.request(prompt="second"), self.out)
        self.assertEqual(comfy.calls, 2, "provider was not retried after cooldown expiry")

    def test_success_clears_a_previous_cooldown(self):
        comfy = FakeProvider("comfyui", fail_times=1)
        router = self.router([comfy], attempts=2, cooldown_seconds=300.0)
        router.generate(self.request(), self.out)
        self.assertNotIn("comfyui", generation._load_provider_state())

    def test_malformed_provider_response_is_an_error_not_a_crash(self):
        class Malformed(FakeProvider):
            def generate(self, request, out_dir, timeout=None):
                self.calls += 1
                return {"assets": []}          # no images, no exception

        bad = Malformed("comfyui")
        proc = FakeProvider("procedural", produces_depicted=False)
        # An empty asset list must not be reported as a success.
        job = self.router([bad, proc], attempts=1).generate(self.request(), self.out)
        self.assertEqual(job["provider"], "procedural")

    # --- job persistence, idempotency, recovery ---

    def test_identical_requests_share_a_job_id(self):
        self.assertEqual(self.request().digest(), self.request().digest())
        self.assertNotEqual(self.request().digest(),
                            self.request(prompt="different").digest())

    def test_completed_job_is_reused_not_regenerated(self):
        comfy = FakeProvider("comfyui")
        router = self.router([comfy])
        first = router.generate(self.request(), self.out)
        second = router.generate(self.request(), self.out)
        self.assertEqual(comfy.calls, 1, "duplicate request regenerated its assets")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertTrue(second.get("reused"))

    def test_reused_job_lands_in_the_new_productions_directory(self):
        """A second production asking for the same picture gets its own copy.

        The defect: the job store is keyed by request digest, so the reused
        record still pointed into the first project's images/ and the new
        production rendered (or failed) without images of its own.
        """
        comfy = FakeProvider("comfyui")
        router = self.router([comfy])
        first = router.generate(self.request(), self.tmp / "project-a" / "images")
        second = router.generate(self.request(), self.tmp / "project-b" / "images")
        self.assertEqual(comfy.calls, 1, "a reused picture was rendered twice")
        self.assertTrue(second.get("reused"))
        for asset in second["assets"]:
            self.assertEqual(Path(asset).parent, (self.tmp / "project-b" / "images").resolve())
            self.assertTrue(Path(asset).is_file())
        self.assertEqual(Path(second["assets"][0]).read_bytes(),
                         Path(first["assets"][0]).read_bytes())
        # The first project keeps its own record and files.
        stored = generation.load_job(first["job_id"])
        self.assertEqual(stored["assets"], first["assets"])
        for asset in first["assets"]:
            Path(asset).unlink()
        self.assertTrue(Path(second["assets"][0]).is_file(),
                        "deleting the first project removed the second's image")

    def test_completed_job_regenerates_when_its_assets_are_gone(self):
        """Reuse is conditional on the output still existing."""
        comfy = FakeProvider("comfyui")
        router = self.router([comfy])
        job = router.generate(self.request(), self.out)
        for asset in job["assets"]:
            Path(asset).unlink()
        router.generate(self.request(), self.out)
        self.assertEqual(comfy.calls, 2)

    def test_interrupted_job_resumes_and_keeps_its_history(self):
        """Simulates the process dying mid-generation."""
        request = self.request()
        generation.save_job({
            "job_id": request.digest(),
            "request": request.to_dict(),
            "status": generation.RUNNING,
            "created_at": generation.utc_now(),
            "attempts": [{"provider": "comfyui", "outcome": "error",
                          "detail": "process killed", "at": generation.utc_now()}],
        })
        proc = FakeProvider("procedural", produces_depicted=False)
        job = self.router([proc]).generate(request, self.out)
        self.assertEqual(job["status"], generation.COMPLETED)
        self.assertEqual(job["attempts"][0]["detail"], "process killed",
                         "prior attempt history was discarded on resume")

    def test_job_state_survives_a_restart(self):
        comfy = FakeProvider("comfyui")
        self.router([comfy]).generate(self.request(), self.out)
        reloaded = generation.load_job(self.request().digest())
        self.assertEqual(reloaded["status"], generation.COMPLETED)
        self.assertEqual(reloaded["provider"], "comfyui")
        self.assertEqual(reloaded["provider_job_id"], "comfyui-job")

    def test_job_records_provider_attempts_and_timing(self):
        comfy = FakeProvider("comfyui", healthy=False)
        proc = FakeProvider("procedural", produces_depicted=False)
        job = self.router([comfy, proc]).generate(self.request(), self.out)
        self.assertIn("elapsed_seconds", job)
        providers_tried = [a["provider"] for a in job["attempts"]]
        self.assertEqual(providers_tried, ["comfyui", "procedural"])

    def test_unknown_provider_in_order_is_ignored(self):
        proc = FakeProvider("procedural", produces_depicted=False)
        router = generation.Router(providers={"procedural": proc},
                                   order=["nonexistent", "procedural"])
        job = router.generate(self.request(), self.out)
        self.assertEqual(job["provider"], "procedural")

    def test_status_reports_every_provider(self):
        comfy = FakeProvider("comfyui", healthy=False)
        proc = FakeProvider("procedural", produces_depicted=False)
        report = self.router([comfy, proc]).status()
        self.assertEqual([e["provider"] for e in report], ["comfyui", "procedural"])
        self.assertFalse(report[0]["healthy"])
        self.assertTrue(report[1]["healthy"])


# --- ComfyUI adapter against a real local server ---------------------------

class FakeComfyHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for the ComfyUI HTTP API."""

    behaviour = {"mode": "ok"}

    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # What the stand-in reports as installed. Tests that need "no checkpoint"
    # or "a different one" override this on the class.
    checkpoints = ["test-checkpoint.safetensors", "other.safetensors"]
    events = []

    def do_GET(self):
        if self.path.startswith("/system_stats"):
            if self.behaviour["mode"] == "unhealthy":
                self._json(500, {"error": "down"})
            else:
                self._json(200, {"system": {"comfyui_version": "test"},
                                 "devices": [{"name": "cuda:0 Fake GPU 3GB",
                                              "vram_total": 3 * 1024 ** 3,
                                              "vram_free": 2 * 1024 ** 3}]})
        elif self.path.startswith("/object_info/CheckpointLoaderSimple"):
            self._json(200, {"CheckpointLoaderSimple": {"input": {"required": {
                "ckpt_name": [list(self.checkpoints), {}]}}}})
        elif self.path.startswith("/history/"):
            prompt_id = self.path.rsplit("/", 1)[-1]
            if self.behaviour["mode"] == "never_finishes":
                self._json(200, {})
            elif self.behaviour["mode"] == "job_error":
                self._json(200, {prompt_id: {"status": {
                    "status_str": "error", "completed": True,
                    "messages": [["execution_start", {}], ["execution_error", {
                        "node_type": "VAEDecode", "exception_type": "RuntimeError",
                        "exception_message": "transient decode failure"}]]}}})
            elif self.behaviour["mode"] == "oom" or (
                    self.behaviour["mode"] == "oom_two_pass"
                    and any(isinstance(n, dict) and n.get("class_type") == "LatentUpscale"
                            for n in ([e for e in self.events if e[0] == "submit"][-1][1]
                                      ["prompt"].values()))):
                self._json(200, {prompt_id: {"status": {
                    "status_str": "error", "completed": True,
                    "messages": [["execution_start", {}], ["execution_error", {
                        "node_type": "KSampler", "exception_type": "torch.OutOfMemoryError",
                        "exception_message": "CUDA out of memory. Tried to allocate 1.2 GiB"}]]}}})
            elif self.behaviour["mode"] == "no_images":
                self._json(200, {prompt_id: {"status": {"completed": True},
                                             "outputs": {}}})
            else:
                self._json(200, {prompt_id: {
                    "status": {"completed": True, "status_str": "success"},
                    "outputs": {"9": {"images": [
                        {"filename": "content-machine_0001.png",
                         "subfolder": "", "type": "output"}]}}}})
        elif self.path.startswith("/view"):
            self.events.append(("download", None))
            body = (ROOT / "tests" / "fixtures" / "images" / "01_red.png").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.events.append(("submit", json.loads(self.rfile.read(length))))
        if self.behaviour["mode"] == "reject":
            self._json(400, {"error": "bad workflow"})
        else:
            self._json(200, {"prompt_id": "test-prompt-1"})


class ComfyUIAdapterTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeComfyHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeComfyHandler.behaviour["mode"] = "ok"
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-comfy-"))
        self.provider = generation.ComfyUIProvider(
            url=self.url,
            workflow_path=str(ROOT / "config" / "comfyui_workflow.example.json"))
        self.request = generation.GenerationRequest(prompt="a lit window", count=1)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_health_ok_when_the_server_answers(self):
        ok, detail = self.provider.health(timeout=5)
        self.assertTrue(ok, detail)

    def test_health_false_when_the_server_errors(self):
        FakeComfyHandler.behaviour["mode"] = "unhealthy"
        ok, _ = self.provider.health(timeout=5)
        self.assertFalse(ok)

    def test_health_false_when_nothing_is_listening(self):
        """The ordinary case: the PC is simply off."""
        offline = generation.ComfyUIProvider(url="http://127.0.0.1:1")
        ok, detail = offline.health(timeout=2)
        self.assertFalse(ok)
        self.assertIn("unreachable", detail)

    def test_unset_url_is_unconfigured(self):
        provider = generation.ComfyUIProvider(url="")
        self.assertFalse(provider.configured())
        ok, detail = provider.health()
        self.assertFalse(ok)
        self.assertIn("COMFYUI_URL", detail)

    def test_generate_submits_polls_and_downloads(self):
        result = self.provider.generate(self.request, self.tmp, timeout=20)
        self.assertEqual(result["provider_job_id"], "test-prompt-1")
        self.assertEqual(len(result["assets"]), 1)
        self.assertTrue(result["assets"][0].is_file())
        self.assertTrue(result["assets"][0].read_bytes().startswith(b"\x89PNG"))

    def test_count_honours_multiple_results_even_with_single_image_workflow(self):
        result = self.provider.generate(
            generation.GenerationRequest(prompt="a lit window", count=2, seed=7),
            self.tmp, timeout=20)
        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual(len(set(map(str, result["assets"]))), 2)

    def test_rejected_workflow_raises(self):
        FakeComfyHandler.behaviour["mode"] = "reject"
        with self.assertRaises(generation.GenerationError):
            self.provider.generate(self.request, self.tmp, timeout=10)

    def test_job_error_raises(self):
        FakeComfyHandler.behaviour["mode"] = "job_error"
        with self.assertRaises(generation.GenerationError):
            self.provider.generate(self.request, self.tmp, timeout=10)

    def test_completed_with_no_images_raises(self):
        FakeComfyHandler.behaviour["mode"] = "no_images"
        with self.assertRaises(generation.GenerationError):
            self.provider.generate(self.request, self.tmp, timeout=10)

    def test_timeout_is_bounded(self):
        """A stalled job must give up, not block the pipeline forever."""
        FakeComfyHandler.behaviour["mode"] = "never_finishes"
        started = time.monotonic()
        with self.assertRaises(generation.GenerationError) as ctx:
            self.provider.generate(self.request, self.tmp, timeout=3)
        elapsed = time.monotonic() - started
        self.assertIn("timed out", str(ctx.exception))
        self.assertLess(elapsed, 15, "timeout was not respected")

    def test_missing_workflow_template_raises(self):
        provider = generation.ComfyUIProvider(
            url=self.url, workflow_path=str(self.tmp / "nope.json"))
        with self.assertRaises(generation.GenerationError):
            provider.generate(self.request, self.tmp, timeout=5)

    def test_prompt_with_quotes_does_not_corrupt_the_workflow(self):
        request = generation.GenerationRequest(
            prompt='a "quoted" prompt\nwith newline', count=1)
        graph = self.provider._load_workflow(request)
        self.assertEqual(graph["6"]["inputs"]["text"], 'a "quoted" prompt\nwith newline')
        self.assertNotIn("_comment", graph)

    def test_workflow_substitutes_numeric_fields(self):
        request = generation.GenerationRequest(prompt="x", width=1280, height=720, seed=7)
        graph = self.provider._load_workflow(request)
        self.assertEqual(graph["5"]["inputs"]["width"], 1280)
        self.assertEqual(graph["5"]["inputs"]["height"], 720)
        self.assertEqual(graph["3"]["inputs"]["seed"], 7)

    def test_load_workflow_honours_a_request_level_override(self):
        """A candidate template can be tried without touching the deployed
        default - the provider's own workflow_path is only the fallback."""
        override = str(ROOT / "config" / "comfyui_workflow_lowvram_upscale.json")
        request = generation.GenerationRequest(
            prompt="x", model="test-checkpoint.safetensors",
            params={"workflow_path": override})
        graph = self.provider._load_workflow(request)
        default_graph = self.provider._load_workflow(generation.GenerationRequest(
            prompt="x", model="test-checkpoint.safetensors"))
        self.assertNotEqual(set(graph.keys()), set(default_graph.keys()))

    def test_load_workflow_override_missing_file_raises(self):
        request = generation.GenerationRequest(
            prompt="x", model="test-checkpoint.safetensors",
            params={"workflow_path": str(self.tmp / "nope.json")})
        with self.assertRaises(generation.GenerationError):
            self.provider._load_workflow(request)


class LowVramWorkflowTestCase(unittest.TestCase):
    """The default graph renders a bounded latent and upscales in-graph, and
    the checkpoint is resolved rather than hard-coded."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeComfyHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeComfyHandler.behaviour["mode"] = "ok"
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-comfy-"))
        self.request = generation.GenerationRequest(prompt="a lit window", count=1)
        self._model_env = os.environ.pop("COMFYUI_MODEL", None)
        self._budget_env = os.environ.pop("COMFYUI_LATENT_MAX_PIXELS", None)
        self._vram_threshold_env = os.environ.pop("COMFYUI_UPSCALE_MIN_VRAM_MB", None)
        # Below the fake device's reported 3GB, so these tests keep exercising
        # the upscale template by default - VRAM-tiered selection has its own
        # test case below.
        os.environ["COMFYUI_UPSCALE_MIN_VRAM_MB"] = "1024"
        FakeComfyHandler.checkpoints = ["test-checkpoint.safetensors", "other.safetensors"]
        self.provider = generation.ComfyUIProvider(url=self.url)   # default workflow

    def tearDown(self):
        if self._model_env is not None:
            os.environ["COMFYUI_MODEL"] = self._model_env
        if self._budget_env is not None:
            os.environ["COMFYUI_LATENT_MAX_PIXELS"] = self._budget_env
        os.environ.pop("COMFYUI_UPSCALE_MIN_VRAM_MB", None)
        if self._vram_threshold_env is not None:
            os.environ["COMFYUI_UPSCALE_MIN_VRAM_MB"] = self._vram_threshold_env
        FakeComfyHandler.checkpoints = ["test-checkpoint.safetensors", "other.safetensors"]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_latent_size_keeps_aspect_within_the_budget(self):
        self.assertEqual(generation.latent_size(1920, 1080, 512 * 512), (680, 384))
        self.assertEqual(generation.latent_size(512, 512, 512 * 512), (512, 512))
        self.assertEqual(generation.latent_size(400, 300, 512 * 512), (400, 300))
        w, h = generation.latent_size(1920, 1080, 768 * 768)
        self.assertEqual((w % 8, h % 8), (0, 0))
        self.assertLessEqual(w * h, 768 * 768)

    def test_default_workflow_renders_small_and_upscales_to_the_request(self):
        request = generation.GenerationRequest(prompt="x", width=1920, height=1080)
        graph = self.provider._load_workflow(request)
        self.assertEqual((graph["5"]["inputs"]["width"], graph["5"]["inputs"]["height"]), (768, 432))
        self.assertEqual((graph["10"]["inputs"]["width"], graph["10"]["inputs"]["height"]), (1920, 1080))
        self.assertEqual(graph["9"]["inputs"]["images"], ["10", 0])
        self.assertEqual(graph["4"]["inputs"]["ckpt_name"], "test-checkpoint.safetensors")

    def test_model_precedence_request_then_env_then_installed(self):
        self.assertEqual(self.provider.resolve_model(
            generation.GenerationRequest(prompt="x", model="other.safetensors")), "other.safetensors")
        os.environ["COMFYUI_MODEL"] = "other.safetensors"
        self.assertEqual(self.provider.resolve_model(generation.GenerationRequest(prompt="x")),
                         "other.safetensors")
        os.environ.pop("COMFYUI_MODEL")
        self.assertEqual(self.provider.resolve_model(generation.GenerationRequest(prompt="x")),
                         "test-checkpoint.safetensors")

    def test_a_checkpoint_comfyui_does_not_have_is_refused_before_rendering(self):
        with self.assertRaises(generation.GenerationError) as ctx:
            self.provider.resolve_model(generation.GenerationRequest(prompt="x", model="nope.ckpt"))
        self.assertIn("checkpoint not installed", str(ctx.exception))

    def test_no_checkpoint_anywhere_is_a_clear_error(self):
        FakeComfyHandler.checkpoints = []
        provider = generation.ComfyUIProvider(url=self.url)
        with self.assertRaises(generation.GenerationError) as ctx:
            provider.resolve_model(generation.GenerationRequest(prompt="x"))
        self.assertIn("no checkpoint available", str(ctx.exception))

    def test_execution_errors_surface_the_real_reason(self):
        FakeComfyHandler.behaviour["mode"] = "oom"
        with self.assertRaises(generation.GenerationError) as ctx:
            self.provider.generate(self.request, self.tmp, timeout=5)
        self.assertIn("CUDA out of memory", str(ctx.exception))
        self.assertIn("KSampler", str(ctx.exception))

    def test_probe_reports_gpu_and_installed_checkpoints(self):
        report = self.provider.probe(timeout=5)
        self.assertTrue(report["reachable"])
        self.assertEqual(report["gpu"], "cuda:0 Fake GPU 3GB")
        self.assertEqual(report["vram_total_mb"], 3072)
        self.assertEqual(report["checkpoints"], ["test-checkpoint.safetensors", "other.safetensors"])
        offline = generation.ComfyUIProvider(url="http://127.0.0.1:1")
        self.assertFalse(offline.probe(timeout=1)["reachable"])

    def test_generate_records_the_upscale_in_its_notes(self):
        result = self.provider.generate(
            generation.GenerationRequest(prompt="x", width=1920, height=1080, count=1),
            self.tmp, timeout=5)
        self.assertEqual(result["model"], "test-checkpoint.safetensors")
        self.assertIn("768x432 -> 1920x1080", result["notes"])
        self.assertTrue(result["upscaled_in_graph"])
        self.assertEqual((result["native_width"], result["native_height"]), (768, 432))


class VramTieredWorkflowTestCase(unittest.TestCase):
    """The worker's own reported VRAM chooses the template automatically -
    the tiny (no-upscale) graph under COMFYUI_UPSCALE_MIN_VRAM_MB, the
    upscale graph at or above it - unless an explicit choice overrides it."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeComfyHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeComfyHandler.behaviour["mode"] = "ok"
        FakeComfyHandler.checkpoints = ["test-checkpoint.safetensors"]
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-comfy-vram-"))
        self._threshold_env = os.environ.pop("COMFYUI_UPSCALE_MIN_VRAM_MB", None)
        self._workflow_env = os.environ.pop("COMFYUI_WORKFLOW", None)
        self._budget_env = os.environ.pop("COMFYUI_LATENT_MAX_PIXELS", None)
        FakeComfyHandler.events = []

    def tearDown(self):
        os.environ.pop("COMFYUI_LATENT_MAX_PIXELS", None)
        if self._budget_env is not None:
            os.environ["COMFYUI_LATENT_MAX_PIXELS"] = self._budget_env
        os.environ.pop("COMFYUI_UPSCALE_MIN_VRAM_MB", None)
        if self._threshold_env is not None:
            os.environ["COMFYUI_UPSCALE_MIN_VRAM_MB"] = self._threshold_env
        if self._workflow_env is not None:
            os.environ["COMFYUI_WORKFLOW"] = self._workflow_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_3gb_card_gets_the_two_pass_template(self):
        """3GB is under the upscale line but over the two-pass floor: the
        verified 768x432 latent is re-sampled at 1.25x, never decoded at
        the full requested size."""
        provider = generation.ComfyUIProvider(url=self.url)
        request = generation.GenerationRequest(prompt="x", width=1920, height=1080)
        graph = provider._load_workflow(request)
        self.assertEqual(graph["5"]["inputs"], {"width": 768, "height": 432, "batch_size": 1})
        self.assertEqual(graph["10"]["class_type"], "LatentUpscale")
        self.assertEqual((graph["10"]["inputs"]["width"], graph["10"]["inputs"]["height"]),
                         (960, 536))
        self.assertEqual(graph["11"]["inputs"]["latent_image"], ["10", 0])
        self.assertLess(graph["11"]["inputs"]["denoise"], 1.0)
        self.assertEqual(graph["8"]["inputs"]["samples"], ["11", 0])
        result = provider.generate(
            generation.GenerationRequest(prompt="x", width=1920, height=1080, count=1),
            self.tmp, timeout=5)
        self.assertEqual((result["native_width"], result["native_height"]), (960, 536))
        self.assert_image_size(result["assets"][0], 1920, 1080)

    def test_two_pass_out_of_memory_falls_back_to_one_pass_for_the_same_request(self):
        FakeComfyHandler.behaviour["mode"] = "oom_two_pass"
        provider = generation.ComfyUIProvider(url=self.url)
        result = provider.generate(
            generation.GenerationRequest(prompt="x", width=1920, height=1080, count=1),
            self.tmp, timeout=5)
        submitted = [payload["prompt"] for kind, payload in FakeComfyHandler.events
                     if kind == "submit"]
        self.assertEqual(len(submitted), 2)
        self.assertIn("10", submitted[0])
        self.assertNotIn("10", submitted[1])
        self.assertEqual((result["native_width"], result["native_height"]), (768, 432))
        self.assertIn("single pass used", result["notes"])

    def test_a_software_failure_on_two_pass_is_not_retried(self):
        FakeComfyHandler.behaviour["mode"] = "job_error"
        provider = generation.ComfyUIProvider(url=self.url)
        with self.assertRaises(generation.GenerationError):
            provider.generate(generation.GenerationRequest(prompt="x", count=1),
                              self.tmp, timeout=5)
        self.assertEqual(len([e for e in FakeComfyHandler.events if e[0] == "submit"]), 1)

    def test_a_small_card_gets_the_tiny_no_upscale_template(self):
        """The fake device reports 3GB; the default threshold (4096) puts it
        below the line, so the deployed default is swapped automatically
        (two-pass disabled here, as an operator can with COMFYUI_HIRES=0)."""
        os.environ["COMFYUI_HIRES"] = "0"
        self.addCleanup(os.environ.pop, "COMFYUI_HIRES", None)
        provider = generation.ComfyUIProvider(url=self.url)
        request = generation.GenerationRequest(prompt="x", width=1920, height=1080)
        graph = provider._load_workflow(request)
        self.assertNotIn("10", graph)
        self.assertEqual(graph["9"]["inputs"]["images"], ["8", 0])
        self.assertEqual(graph["5"]["inputs"], {"width": 768, "height": 432, "batch_size": 1})

    def test_a_large_card_keeps_the_upscale_template(self):
        os.environ["COMFYUI_UPSCALE_MIN_VRAM_MB"] = "1024"
        provider = generation.ComfyUIProvider(url=self.url)
        request = generation.GenerationRequest(prompt="x", width=1920, height=1080)
        graph = provider._load_workflow(request)
        self.assertIn("10", graph)
        self.assertEqual(graph["9"]["inputs"]["images"], ["10", 0])

    def test_an_explicit_workflow_choice_is_never_overridden_by_vram(self):
        upscale_path = str(ROOT / "config" / "comfyui_workflow_lowvram_upscale.json")
        provider = generation.ComfyUIProvider(url=self.url, workflow_path=upscale_path)
        request = generation.GenerationRequest(prompt="x", width=1920, height=1080)
        graph = provider._load_workflow(request)
        self.assertIn("10", graph)

    def test_generate_finishes_full_size_on_cpu_and_records_native_resolution(self):
        os.environ["COMFYUI_HIRES"] = "0"
        self.addCleanup(os.environ.pop, "COMFYUI_HIRES", None)
        provider = generation.ComfyUIProvider(url=self.url)
        result = provider.generate(
            generation.GenerationRequest(prompt="x", width=1920, height=1080, count=1),
            self.tmp, timeout=5)
        self.assertFalse(result["upscaled_in_graph"])
        self.assertTrue(result["upscaled_on_cpu"])
        self.assertEqual((result["native_width"], result["native_height"]), (768, 432))
        self.assertEqual((result["final_width"], result["final_height"]), (1920, 1080))
        self.assertIn("CPU Lanczos upscale", result["notes"])
        self.assert_image_size(result["assets"][0], 1920, 1080)

    def assert_image_size(self, path, width, height):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "json", str(path)],
            check=True, capture_output=True, text=True)
        stream = json.loads(probe.stdout)["streams"][0]
        self.assertEqual((stream["width"], stream["height"]), (width, height))

    def test_multi_image_is_sequential_batch_one_and_each_result_reaches_final_size(self):
        result = generation.ComfyUIProvider(url=self.url).generate(
            generation.GenerationRequest(prompt="moonlit trees", count=3, seed=12),
            self.tmp, timeout=5)
        self.assertEqual([kind for kind, _ in FakeComfyHandler.events],
                         ["submit", "download"] * 3)
        self.assertEqual(len(set(result["assets"])), 3)
        for offset, (_, payload) in enumerate(FakeComfyHandler.events[::2]):
            graph = payload["prompt"]
            self.assertEqual(graph["5"]["inputs"],
                             {"width": 768, "height": 432, "batch_size": 1})
            self.assertEqual(graph["3"]["inputs"]["seed"], 12 + offset)
            self.assert_image_size(result["assets"][offset], 1920, 1080)
        self.assertIn("CPU Lanczos upscale", result["notes"])

    def test_explicit_budget_and_other_hardware_keep_existing_sizing(self):
        request = generation.GenerationRequest(prompt="x")
        provider = generation.ComfyUIProvider(url=self.url)
        os.environ["COMFYUI_LATENT_MAX_PIXELS"] = str(512 * 512)
        self.assertEqual(provider._native_size(request), (680, 384))
        os.environ["COMFYUI_LATENT_MAX_PIXELS"] = str(1024 * 1024)
        self.assertEqual(provider._native_size(request), generation.latent_size(1920, 1080))
        os.environ.pop("COMFYUI_LATENT_MAX_PIXELS")
        for memory in (2048, 8192, None):
            with patch.object(provider, "vram_total_mb", return_value=memory):
                self.assertEqual(provider._native_size(request), (680, 384))
        self.assertEqual(provider._native_size(
            generation.GenerationRequest(prompt="x", width=512, height=512)), (512, 512))

    def test_cpu_resize_failure_preserves_original_and_is_not_success(self):
        provider = generation.ComfyUIProvider(url=self.url)
        with patch.object(generation.subprocess, "run", side_effect=FileNotFoundError("ffmpeg missing")):
            with self.assertRaisesRegex(generation.GenerationError, "preparing its final size failed"):
                provider.generate(generation.GenerationRequest(prompt="x"), self.tmp, timeout=5)
        self.assertEqual(len(list(self.tmp.glob("*.png"))), 1)
        self.assertFalse(list(self.tmp.glob("*.sized.png")))

    def test_an_unreachable_worker_keeps_the_deployed_default(self):
        """A card that cannot say its VRAM is not guessed at."""
        provider = generation.ComfyUIProvider(url="http://127.0.0.1:1")
        self.assertIsNone(provider.vram_total_mb(timeout=1))
        self.assertEqual(provider._select_workflow_path(timeout=1), provider.workflow_path)


class FailureClassificationTestCase(unittest.TestCase):

    def test_vram_exhaustion_is_capacity(self):
        self.assertEqual(generation.classify_failure(
            "torch.OutOfMemoryError: Allocation on device 0 would exceed "
            "allowed memory. (out of memory)"), "capacity")

    def test_missing_checkpoint_is_software(self):
        self.assertEqual(generation.classify_failure(
            "checkpoint not installed in ComfyUI: 'nope.ckpt'"), "software")

    def test_no_detail_is_unclassified(self):
        self.assertIsNone(generation.classify_failure(None))
        self.assertIsNone(generation.classify_failure(""))


class ProceduralProviderTestCase(unittest.TestCase):
    """The always-on fallback: must work with nothing but ffmpeg."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg not found on PATH")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-proc-"))
        self.provider = generation.ProceduralProvider()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_always_available(self):
        ok, _ = self.provider.health()
        self.assertTrue(ok)
        self.assertTrue(self.provider.configured())

    def test_never_claims_to_produce_depicted_imagery(self):
        self.assertFalse(self.provider.produces_depicted)

    def test_costs_nothing(self):
        self.assertFalse(self.provider.costs_money)

    def test_generates_stamped_plates(self):
        request = generation.GenerationRequest(prompt="ignored", count=2)
        result = self.provider.generate(request, self.tmp)
        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual(result["cost_usd"], 0.0)
        for asset in result["assets"]:
            # The stamp is what stops a plate being relabelled as a real asset.
            kind, _ = generation.make_visuals.classify(asset)
            self.assertEqual(kind, "procedural")

    def test_unknown_style_falls_back_to_a_valid_one(self):
        request = generation.GenerationRequest(prompt="x", count=1, style="not-a-style")
        result = self.provider.generate(request, self.tmp)
        self.assertEqual(len(result["assets"]), 1)


class ApiProviderTestCase(unittest.TestCase):
    """The paid provider must stay inert until deliberately configured."""

    def test_unconfigured_by_default(self):
        provider = generation.ApiProvider(url="", api_key="")
        self.assertFalse(provider.configured())
        ok, detail = provider.health()
        self.assertFalse(ok)
        self.assertIn("disabled", detail)

    def test_declares_that_it_costs_money(self):
        self.assertTrue(generation.ApiProvider.costs_money)

    def test_health_does_not_call_the_metered_endpoint(self):
        """Probing a paid endpoint would itself cost money."""
        provider = generation.ApiProvider(
            url="http://127.0.0.1:1/generate", api_key="secret")
        ok, _ = provider.health(timeout=2)
        self.assertTrue(ok, "health should be config-only, not a network probe")

    def test_is_last_in_the_default_routing_order(self):
        self.assertEqual(generation.DEFAULT_ORDER[-1], "api")


# --- Gemini adapter against a real local server ----------------------------

# A one-pixel PNG. Small, but a genuine decodable file, so a test that says
# "an image was written" is saying something true.
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")


class FakeGeminiHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for the Gemini generateContent API."""

    behaviour = {"mode": "ok"}
    seen = []

    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append({
            "path": self.path,
            "api_key_header": self.headers.get("x-goog-api-key"),
            "payload": payload,
        })
        mode = self.behaviour["mode"]
        if mode == "http_error":
            self._json(429, {"error": {"message": "quota exhausted"}})
        elif mode == "blocked":
            self._json(200, {"promptFeedback": {"blockReason": "SAFETY"}})
        elif mode == "no_candidates":
            self._json(200, {"candidates": []})
        elif mode == "text_only":
            self._json(200, {"candidates": [{
                "finishReason": "PROHIBITED_CONTENT",
                "content": {"parts": [{"text": "I can't make that."}]}}]})
        elif mode == "bad_mime":
            self._json(200, {"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/webp",
                                "data": base64.b64encode(ONE_PIXEL_PNG).decode()}}]}}]})
        else:
            self._json(200, {
                "responseId": "resp-1",
                "modelVersion": "test-image-model",
                "usageMetadata": {"totalTokenCount": 42},
                "candidates": [{"content": {"parts": [
                    {"inlineData": {"mimeType": "image/png",
                                    "data": base64.b64encode(ONE_PIXEL_PNG).decode()}}]}}],
            })


class GeminiProviderGateTestCase(unittest.TestCase):
    """The spend gate. A key that exists for another purpose must not buy images."""

    def test_a_key_alone_does_not_enable_it(self):
        """GEMINI_API_KEY is already set for the creative brief. If that were
        enough, enabling AI titles would silently enable paid image generation."""
        provider = generation.GeminiProvider(api_key="secret", enabled=False)
        self.assertFalse(provider.configured())
        ok, detail = provider.health()
        self.assertFalse(ok)
        self.assertIn("GEMINI_IMAGE_ENABLED", detail)

    def test_consent_without_a_key_is_not_configured(self):
        provider = generation.GeminiProvider(api_key="", enabled=True)
        self.assertFalse(provider.configured())
        ok, detail = provider.health()
        self.assertFalse(ok)
        self.assertIn("KEY", detail.upper())

    def test_both_together_configure_it(self):
        provider = generation.GeminiProvider(api_key="secret", enabled=True)
        self.assertTrue(provider.configured())
        self.assertTrue(provider.health()[0])

    def test_declares_what_it_is(self):
        self.assertTrue(generation.GeminiProvider.costs_money)
        self.assertTrue(generation.GeminiProvider.produces_depicted)

    def test_health_does_not_call_the_metered_endpoint(self):
        provider = generation.GeminiProvider(
            api_key="secret", enabled=True, base_url="http://127.0.0.1:1")
        ok, _ = provider.health(timeout=2)
        self.assertTrue(ok, "health should be config-only, not a network probe")

    def test_every_paid_provider_comes_after_every_free_one(self):
        providers = generation.build_providers()
        paid = [i for i, n in enumerate(generation.DEFAULT_ORDER)
                if providers[n].costs_money]
        free = [i for i, n in enumerate(generation.DEFAULT_ORDER)
                if not providers[n].costs_money]
        self.assertTrue(paid and free)
        self.assertGreater(min(paid), max(free))


class GeminiAdapterTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeGeminiHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeGeminiHandler.behaviour = {"mode": "ok"}
        FakeGeminiHandler.seen = []
        self.tmp = tempfile.mkdtemp(prefix="cm-gemini-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Redirect job/cooldown state so tests never touch the real jobs/.
        self._orig_jobs = generation.JOBS_DIR
        self._orig_state = generation.PROVIDER_STATE_PATH
        generation.JOBS_DIR = Path(self.tmp) / "jobs"
        generation.PROVIDER_STATE_PATH = generation.JOBS_DIR / "_provider_state.json"
        self.provider = generation.GeminiProvider(
            api_key="secret", enabled=True, base_url=self.url, model="test-model")

    def tearDown(self):
        generation.JOBS_DIR = self._orig_jobs
        generation.PROVIDER_STATE_PATH = self._orig_state

    def _request(self, **kwargs):
        kwargs.setdefault("prompt", "a dark empty road at night")
        return generation.GenerationRequest(**kwargs)

    def test_writes_a_real_png(self):
        result = self.provider.generate(self._request(), self.tmp, timeout=10)
        self.assertEqual(len(result["assets"]), 1)
        asset = Path(result["assets"][0])
        self.assertTrue(asset.is_file())
        self.assertEqual(asset.suffix, ".png")
        self.assertEqual(asset.read_bytes(), ONE_PIXEL_PNG)
        self.assertEqual(result["model"], "test-image-model")
        self.assertEqual(result["provider_job_id"], "resp-1")

    def test_cost_is_unknown_not_invented(self):
        """The API reports tokens, not money. A fabricated dollar figure in
        provenance would be worse than an honest None."""
        result = self.provider.generate(self._request(), self.tmp, timeout=10)
        self.assertIsNone(result["cost_usd"])
        self.assertIn("42 token(s)", result["notes"])

    def test_count_is_one_call_per_image(self):
        result = self.provider.generate(self._request(count=3), self.tmp, timeout=10)
        self.assertEqual(len(result["assets"]), 3)
        self.assertEqual(len({str(a) for a in result["assets"]}), 3)
        self.assertEqual(len(FakeGeminiHandler.seen), 3)

    def test_key_travels_in_a_header_not_the_url(self):
        self.provider.generate(self._request(), self.tmp, timeout=10)
        call = FakeGeminiHandler.seen[0]
        self.assertEqual(call["api_key_header"], "secret")
        self.assertNotIn("secret", call["path"])

    def test_model_is_in_the_path(self):
        self.provider.generate(self._request(), self.tmp, timeout=10)
        self.assertEqual(FakeGeminiHandler.seen[0]["path"],
                         "/models/test-model:generateContent")

    def test_negative_prompt_reaches_the_model(self):
        """The API has no negative-prompt field; dropping it silently would
        generate imagery the caller explicitly excluded."""
        self.provider.generate(
            self._request(negative_prompt="text, watermark"), self.tmp, timeout=10)
        text = FakeGeminiHandler.seen[0]["payload"]["contents"][0]["parts"][0]["text"]
        self.assertIn("text, watermark", text)

    def test_pixel_size_becomes_the_nearest_aspect_ratio(self):
        self.provider.generate(
            self._request(width=1920, height=1080), self.tmp, timeout=10)
        config = FakeGeminiHandler.seen[0]["payload"]["generationConfig"]["imageConfig"]
        self.assertEqual(config["aspectRatio"], "16:9")
        self.assertNotIn("imageSize", config,
                         "an unset image size must not be chosen on the operator's behalf")

    def test_vertical_request_maps_to_a_vertical_ratio(self):
        self.provider.generate(
            self._request(width=1080, height=1920), self.tmp, timeout=10)
        self.assertEqual(
            FakeGeminiHandler.seen[0]["payload"]["generationConfig"]["imageConfig"]["aspectRatio"],
            "9:16")

    def test_image_size_is_sent_when_the_operator_sets_it(self):
        provider = generation.GeminiProvider(
            api_key="secret", enabled=True, base_url=self.url,
            model="test-model", image_size="2K")
        provider.generate(self._request(), self.tmp, timeout=10)
        self.assertEqual(
            FakeGeminiHandler.seen[0]["payload"]["generationConfig"]["imageConfig"]["imageSize"],
            "2K")

    def test_a_refusal_is_an_error_not_an_empty_success(self):
        FakeGeminiHandler.behaviour = {"mode": "blocked"}
        with self.assertRaises(generation.GenerationError) as ctx:
            self.provider.generate(self._request(), self.tmp, timeout=10)
        self.assertIn("SAFETY", str(ctx.exception))

    def test_a_text_only_reply_is_an_error(self):
        FakeGeminiHandler.behaviour = {"mode": "text_only"}
        with self.assertRaises(generation.GenerationError) as ctx:
            self.provider.generate(self._request(), self.tmp, timeout=10)
        self.assertIn("PROHIBITED_CONTENT", str(ctx.exception))

    def test_no_candidates_is_an_error(self):
        FakeGeminiHandler.behaviour = {"mode": "no_candidates"}
        with self.assertRaises(generation.GenerationError):
            self.provider.generate(self._request(), self.tmp, timeout=10)

    def test_an_unrenderable_image_type_is_refused(self):
        """render.py reads png/jpg only; writing a .webp would fail much later."""
        FakeGeminiHandler.behaviour = {"mode": "bad_mime"}
        with self.assertRaises(generation.GenerationError) as ctx:
            self.provider.generate(self._request(), self.tmp, timeout=10)
        self.assertIn("webp", str(ctx.exception))

    def test_http_error_is_reported_without_leaking_the_key(self):
        FakeGeminiHandler.behaviour = {"mode": "http_error"}
        with self.assertRaises(generation.GenerationError) as ctx:
            self.provider.generate(self._request(), self.tmp, timeout=10)
        message = str(ctx.exception)
        self.assertIn("429", message)
        self.assertIn("quota exhausted", message)
        self.assertNotIn("secret", message)

    def test_router_fails_over_past_a_broken_gemini(self):
        """A depicted request that Gemini cannot satisfy must surface as a
        routing failure, not a half-written job."""
        FakeGeminiHandler.behaviour = {"mode": "http_error"}
        router = generation.Router(
            providers={"gemini": self.provider}, order=["gemini"], attempts=1,
            cooldown_seconds=0)
        with self.assertRaises(generation.GenerationError):
            router.generate(self._request(require_depicted=True), Path(self.tmp) / "out")

    def test_router_reaches_gemini_for_a_depicted_request(self):
        router = generation.Router(
            providers={"procedural": generation.ProceduralProvider(),
                       "gemini": self.provider},
            order=["procedural", "gemini"], attempts=1, cooldown_seconds=0)
        job = router.generate(self._request(require_depicted=True), Path(self.tmp) / "out")
        self.assertEqual(job["provider"], "gemini",
                         "procedural is abstract-only and must be skipped")
        self.assertTrue(job["produces_depicted"])


class ProjectVisualsTestCase(unittest.TestCase):
    """The `visuals` command's contract with the publication gate.

    The production-grade claim is the hinge the whole review gate turns on,
    so its lifecycle across regenerations is tested directly.
    """

    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import project
        self.project = project
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-vis-"))
        self._orig_projects = project.PROJECTS_DIR
        self._orig_jobs = generation.JOBS_DIR
        self._orig_state = generation.PROVIDER_STATE_PATH
        project.PROJECTS_DIR = self.tmp / "projects"
        generation.JOBS_DIR = self.tmp / "jobs"
        generation.PROVIDER_STATE_PATH = generation.JOBS_DIR / "_provider_state.json"

        self.pdir = project.PROJECTS_DIR / "vis-test"
        (self.pdir / "images").mkdir(parents=True)
        (self.pdir / "video_spec.json").write_text(json.dumps({
            "width": 1920, "height": 1080, "fps": 30, "duration_seconds": 10,
            "images": {"source_dir": "images", "seconds_per_image": 4.0},
            "audio": {"file": "audio/track.wav"}}))
        (self.pdir / "metadata.json").write_text(json.dumps({
            "video_id": "vis-test", "status": {}, "provenance": {},
            "visual_plan": {"prompt": "a lit window", "count": 1}}))

    def tearDown(self):
        self.project.PROJECTS_DIR = self._orig_projects
        generation.JOBS_DIR = self._orig_jobs
        generation.PROVIDER_STATE_PATH = self._orig_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def metadata(self):
        return json.loads((self.pdir / "metadata.json").read_text())

    def args(self, **kw):
        defaults = dict(video_id="vis-test", prompt=None, negative=None, count=None,
                        width=None, height=None, seed=None, model=None, style=None,
                        depicted=False)
        defaults.update(kw)
        return type("Args", (), defaults)()

    def run_visuals(self, providers, order, **kw):
        original = generation.Router

        def patched(*a, **k):
            return original(providers={p.name: p for p in providers}, order=order)

        generation.Router = patched
        try:
            return self.project.cmd_visuals(self.args(**kw))
        finally:
            generation.Router = original

    def test_abstract_provider_records_not_production_grade(self):
        proc = FakeProvider("procedural", produces_depicted=False)
        self.assertEqual(self.run_visuals([proc], ["procedural"]), 0)
        images = self.metadata()["provenance"]["images"]
        self.assertIs(images["production_grade"], False)
        self.assertEqual(images["provider"], "procedural")

    def test_depicted_provider_leaves_the_claim_for_a_human(self):
        """A machine cannot certify an asset as production-grade."""
        comfy = FakeProvider("comfyui")
        self.assertEqual(self.run_visuals([comfy], ["comfyui"]), 0)
        self.assertIsNone(self.metadata()["provenance"]["images"]["production_grade"])

    def test_new_assets_clear_a_stale_claim(self):
        """A claim describes specific assets; different assets void it."""
        comfy = FakeProvider("comfyui")
        self.run_visuals([comfy], ["comfyui"], prompt="first")
        meta = self.metadata()
        meta["provenance"]["images"]["production_grade"] = True
        (self.pdir / "metadata.json").write_text(json.dumps(meta))

        self.run_visuals([comfy], ["comfyui"], prompt="a different scene")
        self.assertIsNone(self.metadata()["provenance"]["images"]["production_grade"],
                          "a human claim survived onto assets it was never made about")

    def test_reused_job_preserves_an_existing_claim(self):
        """Identical inputs mean identical assets - the claim still holds."""
        comfy = FakeProvider("comfyui")
        self.run_visuals([comfy], ["comfyui"])
        meta = self.metadata()
        meta["provenance"]["images"]["production_grade"] = True
        (self.pdir / "metadata.json").write_text(json.dumps(meta))

        self.run_visuals([comfy], ["comfyui"])
        self.assertIs(self.metadata()["provenance"]["images"]["production_grade"], True)

    def test_failover_to_abstract_downgrades_a_previous_claim(self):
        """The PC going offline must not leave a stale production-grade claim."""
        comfy = FakeProvider("comfyui")
        self.run_visuals([comfy], ["comfyui"])
        meta = self.metadata()
        meta["provenance"]["images"]["production_grade"] = True
        (self.pdir / "metadata.json").write_text(json.dumps(meta))

        offline = FakeProvider("comfyui", healthy=False)
        proc = FakeProvider("procedural", produces_depicted=False)
        self.run_visuals([offline, proc], ["comfyui", "procedural"], prompt="regenerate")
        images = self.metadata()["provenance"]["images"]
        self.assertEqual(images["provider"], "procedural")
        self.assertIs(images["production_grade"], False)

    def test_failure_records_status_and_returns_nonzero(self):
        broken = FakeProvider("comfyui", fail_times=99)
        self.assertEqual(self.run_visuals([broken], ["comfyui"]), 1)
        self.assertEqual(self.metadata()["status"]["visuals"], "FAILED")

    def test_missing_prompt_is_rejected(self):
        meta = self.metadata()
        del meta["visual_plan"]
        (self.pdir / "metadata.json").write_text(json.dumps(meta))
        proc = FakeProvider("procedural", produces_depicted=False)
        self.assertEqual(self.run_visuals([proc], ["procedural"]), 1)


if __name__ == "__main__":
    unittest.main()


class ProceduralProviderHonoursRequestedSizeTestCase(unittest.TestCase):
    """A storyboard scene asks for its own source size; the plate must be
    that size or storyboard QC's scene_image_dimensions check fails."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise unittest.SkipTest("ffmpeg/ffprobe not found on PATH")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-proc-size-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _dimensions(self, path):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True).stdout.strip()
        return tuple(int(v) for v in out.split(","))

    def test_generates_at_the_requested_source_size(self):
        request = generation.GenerationRequest(prompt="x", width=512, height=512, seed=3)
        job = generation.ProceduralProvider().generate(request, self.tmp)
        self.assertEqual(self._dimensions(job["assets"][0]), (512, 512))

    def test_default_request_still_renders_the_output_resolution(self):
        request = generation.GenerationRequest(prompt="x", seed=3)
        job = generation.ProceduralProvider().generate(request, self.tmp)
        self.assertEqual(self._dimensions(job["assets"][0]), (request.width, request.height))

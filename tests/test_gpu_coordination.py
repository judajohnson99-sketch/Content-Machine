#!/usr/bin/env python3
"""scripts/gpu_coordination.py: the lease and the ComfyUI/VRAM probes."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gpu_coordination as gpu  # noqa: E402


class FakeComfy(BaseHTTPRequestHandler):
    queue = {"queue_running": [], "queue_pending": []}
    stats = {"devices": [{"vram_free": 1500 * 1024 * 1024}]}
    freed = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        type(self).freed.append((self.path, json.loads(self.rfile.read(length) or b"{}")))
        self.send_response(200)
        self.end_headers()

    def do_GET(self):  # noqa: N802
        body = {"/queue": type(self).queue, "/system_stats": type(self).stats}.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class LeaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "gpu.lock"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_second_try_lock_is_refused_and_names_the_holder(self):
        with gpu.lease("worker-agent", path=self.path):
            with self.assertRaises(gpu.GpuBusy) as caught:
                with gpu.lease("vision", path=self.path):
                    self.fail("the lease was granted twice")
        self.assertEqual(caught.exception.reason, gpu.BUSY_LEASE)
        self.assertIn("worker-agent", caught.exception.detail)

    def test_the_lease_is_free_again_after_release(self):
        with gpu.lease("a", path=self.path):
            pass
        with gpu.lease("b", path=self.path):
            self.assertEqual(gpu.holder(self.path)["who"], "b")
        self.assertIsNone(gpu.holder(self.path))

    def test_a_waiting_caller_gets_it_when_the_holder_lets_go(self):
        got = []

        def second():
            with gpu.lease("second", wait_seconds=5, path=self.path):
                got.append(time.monotonic())

        with gpu.lease("first", path=self.path):
            thread = threading.Thread(target=second)
            thread.start()
            time.sleep(0.6)
            self.assertEqual(got, [], "the waiter took the lease while it was held")
        thread.join(5)
        self.assertEqual(len(got), 1)

    def test_a_waiting_caller_gives_up_at_its_deadline(self):
        with gpu.lease("first", path=self.path):
            started = time.monotonic()
            with self.assertRaises(gpu.GpuBusy):
                with gpu.lease("second", wait_seconds=0.5, path=self.path):
                    pass
            self.assertGreaterEqual(time.monotonic() - started, 0.45)

    def test_the_lock_path_follows_the_jobs_dir(self):
        with mock.patch.dict(os.environ, {"CONTENT_MACHINE_JOBS_DIR": "/somewhere/jobs"}):
            os.environ.pop("GPU_LOCK_PATH", None)
            self.assertEqual(gpu.lock_path(), Path("/somewhere/jobs/_gpu.lock"))
        with mock.patch.dict(os.environ, {"GPU_LOCK_PATH": "/x/gpu.lock"}):
            self.assertEqual(gpu.lock_path(), Path("/x/gpu.lock"))


class ProbeTest(unittest.TestCase):
    def setUp(self):
        FakeComfy.queue = {"queue_running": [], "queue_pending": []}
        FakeComfy.stats = {"devices": [{"vram_free": 1500 * 1024 * 1024}]}
        self.server = HTTPServer(("127.0.0.1", 0), FakeComfy)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_idle_comfyui_is_not_busy(self):
        self.assertEqual(gpu.comfyui_busy(self.url), (False, "ComfyUI is idle"))

    def test_running_or_queued_work_is_busy(self):
        FakeComfy.queue = {"queue_running": [[0, "id"]], "queue_pending": []}
        busy, detail = gpu.comfyui_busy(self.url)
        self.assertTrue(busy)
        self.assertIn("1 running", detail)
        FakeComfy.queue = {"queue_running": [], "queue_pending": [[1], [2]]}
        self.assertIn("2 queued", gpu.comfyui_busy(self.url)[1])

    def test_unset_or_unreachable_comfyui_is_not_busy(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COMFYUI_URL", None)
            self.assertFalse(gpu.comfyui_busy()[0])
        busy, detail = gpu.comfyui_busy("http://127.0.0.1:9", timeout=1)
        self.assertFalse(busy)
        self.assertIn("not reachable", detail)

    def test_free_vram_comes_from_comfyui(self):
        self.assertEqual(gpu.free_vram_mb(self.url), 1500)

    def test_free_vram_is_unknown_when_nothing_can_say(self):
        with mock.patch.object(gpu.shutil, "which", return_value=None):
            self.assertIsNone(gpu.free_vram_mb("http://127.0.0.1:9", timeout=1))

    def test_free_comfyui_asks_it_to_unload_its_models(self):
        FakeComfy.freed.clear()
        self.assertTrue(gpu.free_comfyui(self.url))
        self.assertEqual(FakeComfy.freed, [("/free", {"unload_models": True, "free_memory": True})])

    def test_free_comfyui_is_false_when_there_is_nothing_to_free(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COMFYUI_URL", None)
            self.assertFalse(gpu.free_comfyui())
        self.assertFalse(gpu.free_comfyui("http://127.0.0.1:9", timeout=1))

    def test_check_clear_refuses_busy_comfyui_and_too_little_vram(self):
        FakeComfy.queue = {"queue_running": [[0]], "queue_pending": []}
        with self.assertRaises(gpu.GpuBusy) as caught:
            gpu.check_clear(0, self.url)
        self.assertEqual(caught.exception.reason, gpu.BUSY_COMFYUI)
        FakeComfy.queue = {"queue_running": [], "queue_pending": []}
        with self.assertRaises(gpu.GpuBusy) as caught:
            gpu.check_clear(2500, self.url)
        self.assertEqual(caught.exception.reason, gpu.BUSY_VRAM)
        self.assertIn("1500 MB", caught.exception.detail)
        self.assertTrue(gpu.check_clear(1000, self.url))

    def test_unknown_vram_does_not_block_a_clear_gpu(self):
        with mock.patch.object(gpu, "free_vram_mb", return_value=None):
            self.assertTrue(gpu.check_clear(2500, self.url))


if __name__ == "__main__":
    unittest.main()

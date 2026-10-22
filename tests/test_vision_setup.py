#!/usr/bin/env python3
"""scripts/vision_setup.py: doctor, env, pull, smoke and service, with no real Ollama."""
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import envfile  # noqa: E402
import vision  # noqa: E402
import vision_setup as setup  # noqa: E402

GOOD = {
    "adherence": 5, "adherence_reason": "a red circle is on the left", "composition": 4,
    "composition_reason": "clear", "artifacts": [], "artifact_reason": "",
    "style_medium": "illustration", "style_palette": ["black", "red"],
    "style_lighting": "dim", "style_mood": "calm",
}


class FakeOllama(BaseHTTPRequestHandler):
    installed = ["qwen3-vl:2b"]
    generate_replies = []          # consumed in order by /api/generate
    pull_events = []
    posts = []

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        data = json.dumps({"models": [{"name": n} for n in type(self).installed]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).posts.append((self.path, body))
        self.send_response(200)
        self.end_headers()
        if self.path == "/api/pull":
            for event in type(self).pull_events:
                self.wfile.write((json.dumps(event) + "\n").encode())
            return
        reply = type(self).generate_replies.pop(0) if type(self).generate_replies else GOOD
        self.wfile.write(json.dumps({"response": json.dumps(reply)}).encode())


class SetupTestCase(unittest.TestCase):
    def setUp(self):
        FakeOllama.installed = ["qwen3-vl:2b"]
        FakeOllama.generate_replies = []
        FakeOllama.pull_events = []
        FakeOllama.posts = []
        self.server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {
            "CONTENT_MACHINE_JOBS_DIR": str(self.dir / "jobs"),
            "GPU_LOCK_PATH": str(self.dir / "gpu.lock"),
            "QUALITY_VISION_FREE_SETTLE_SECONDS": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("QUALITY_VISION_URL", "QUALITY_VISION_MODELS", "QUALITY_VISION_MODEL",
                     "COMFYUI_URL", "WORKER_VISION", "CONTROL_PLANE_URL", "WORKER_TOKEN"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def by_name(self, results):
        return {r["name"]: r for r in results}


class DoctorTest(SetupTestCase):
    def test_a_ready_machine_has_nothing_to_fix(self):
        FakeOllama.installed = ["qwen3-vl:2b", "moondream:latest"]
        os.environ.update({"QUALITY_VISION_URL": self.url, "WORKER_VISION": "1",
                           "CONTROL_PLANE_URL": "http://127.0.0.1:8788", "WORKER_TOKEN": "x",
                           "COMFYUI_URL": "http://127.0.0.1:9"})
        results = setup.doctor()
        checks = self.by_name(results)
        self.assertEqual(checks["ollama server"]["status"], setup.PASS)
        self.assertEqual(checks["vision models"]["status"], setup.PASS)
        self.assertEqual(checks["worker vision flag"]["status"], setup.PASS)
        self.assertEqual(checks["control plane"]["status"], setup.PASS)
        self.assertEqual(checks["calibration dataset"]["status"], setup.PASS)
        self.assertEqual(checks["gpu lease file"]["status"], setup.PASS)
        self.assertFalse([r for r in results if r["status"] == setup.FAIL], results)

    def test_calibration_is_reported_per_installed_model_with_the_command_to_run(self):
        os.environ["QUALITY_VISION_URL"] = self.url
        check = self.by_name(setup.doctor())["calibration: qwen3-vl:2b"]
        self.assertEqual(check["status"], setup.WARN)
        self.assertIn("nothing yet", check["detail"])
        self.assertIn("vision_calibration.py run --model qwen3-vl:2b --apply", check["hint"])

    def test_no_installed_model_is_a_failure_with_the_pull_command(self):
        FakeOllama.installed = ["llama3:8b"]
        os.environ["QUALITY_VISION_URL"] = self.url
        check = self.by_name(setup.doctor())["vision models"]
        self.assertEqual(check["status"], setup.FAIL)
        self.assertIn("vision_setup.py pull", check["hint"])

    def test_a_missing_fallback_is_only_a_warning(self):
        os.environ["QUALITY_VISION_URL"] = self.url
        check = self.by_name(setup.doctor())["vision models"]
        self.assertEqual(check["status"], setup.WARN)
        self.assertIn("moondream", check["detail"])

    def test_an_unset_url_says_so_and_points_at_env_write(self):
        checks = self.by_name(setup.doctor())
        self.assertEqual(checks["QUALITY_VISION_URL"]["status"], setup.WARN)
        self.assertIn("env --write", checks["QUALITY_VISION_URL"]["hint"])

    def test_a_configured_but_unreachable_ollama_is_a_failure(self):
        os.environ["QUALITY_VISION_URL"] = "http://127.0.0.1:9"
        check = self.by_name(setup.doctor())["ollama server"]
        self.assertEqual(check["status"], setup.FAIL)
        self.assertIn("ollama serve", check["hint"])

    def test_the_doctor_changes_nothing_on_the_machine(self):
        comfy_posts = []

        class Comfy(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"queue_running": [], "queue_pending": []}')

            def do_POST(self):  # noqa: N802
                comfy_posts.append(self.path)
                self.send_response(200)
                self.end_headers()
        comfy = HTTPServer(("127.0.0.1", 0), Comfy)
        threading.Thread(target=comfy.serve_forever, daemon=True).start()
        self.addCleanup(comfy.server_close)
        self.addCleanup(comfy.shutdown)
        os.environ.update({"QUALITY_VISION_URL": self.url,
                           "COMFYUI_URL": f"http://127.0.0.1:{comfy.server_port}"})
        setup.doctor()
        self.assertEqual(comfy_posts, [], "the doctor asked ComfyUI to unload its models")
        self.assertEqual(FakeOllama.posts, [], "the doctor asked Ollama to do something")

    def test_the_exit_code_reflects_only_failures(self):
        ok = [setup.check("a", setup.PASS, "fine"), setup.check("b", setup.WARN, "meh", "do x")]
        bad = ok + [setup.check("c", setup.FAIL, "broken", "fix y")]
        with mock.patch("builtins.print"):
            self.assertEqual(setup.print_doctor(ok), 0)
            self.assertEqual(setup.print_doctor(bad), 1)


class EnvTest(SetupTestCase):
    def test_write_adds_the_missing_lines_and_is_idempotent(self):
        path = self.dir / ".env"
        path.write_text("COMFYUI_URL=http://127.0.0.1:8188")
        added, kept = setup.write_env(path)
        self.assertEqual(set(added), {k for k, _, _ in setup.ENV_BLOCK})
        values = envfile.parse_env_file(path)
        self.assertEqual(values["COMFYUI_URL"], "http://127.0.0.1:8188")
        self.assertEqual(values["QUALITY_VISION_URL"], setup.OLLAMA_LOCAL)
        self.assertEqual(values["QUALITY_VISION_MODELS"], "qwen3-vl:2b,moondream")
        self.assertEqual(values["WORKER_VISION"], "1")
        again_added, again_kept = setup.write_env(path)
        self.assertEqual(again_added, [])
        self.assertEqual(set(again_kept), {k for k, _, _ in setup.ENV_BLOCK})

    def test_an_existing_value_is_never_overwritten(self):
        path = self.dir / ".env"
        path.write_text("QUALITY_VISION_MODELS=moondream\nQUALITY_VISION_URL=http://10.0.0.5:11434\n")
        added, kept = setup.write_env(path)
        values = envfile.parse_env_file(path)
        self.assertEqual(values["QUALITY_VISION_MODELS"], "moondream")
        self.assertEqual(values["QUALITY_VISION_URL"], "http://10.0.0.5:11434")
        self.assertNotIn("QUALITY_VISION_URL", added)
        self.assertIn("QUALITY_VISION_URL", kept)

    def test_a_missing_file_is_created(self):
        path = self.dir / "new.env"
        setup.write_env(path)
        self.assertTrue(path.is_file())
        self.assertIn("QUALITY_VISION_URL", envfile.parse_env_file(path))

    def test_every_key_it_proposes_is_actually_read_by_the_code(self):
        source = "\n".join(p.read_text() for p in (ROOT / "scripts").glob("*.py")
                           if p.name not in ("vision_setup.py",))
        for key, _, _ in setup.ENV_BLOCK:
            self.assertRegex(source, rf"[\"']{re.escape(key)}[\"']",
                             f"{key} is proposed for .env but nothing reads it")

    def test_the_proposed_url_is_written_down_not_defaulted_in_code(self):
        self.assertFalse(vision.OllamaProvider().configured())
        self.assertIsNone(vision.runner_from_env())


class PullTest(SetupTestCase):
    def test_progress_is_reported_and_success_ends_it(self):
        FakeOllama.pull_events = [
            {"status": "pulling manifest"},
            {"status": "pulling abc", "total": 1000, "completed": 100},
            {"status": "pulling abc", "total": 1000, "completed": 500},
            {"status": "pulling abc", "total": 1000, "completed": 1000},
            {"status": "verifying sha256 digest"}, {"status": "success"}]
        lines = []
        ok, detail = setup.pull_model(self.url, "qwen3-vl:2b", out=lines.append)
        self.assertEqual((ok, detail), (True, "downloaded"))
        self.assertIn("  qwen3-vl:2b: pulling manifest", lines)
        self.assertTrue(any("100%" in l for l in lines))
        self.assertEqual(FakeOllama.posts[-1][1]["model"], "qwen3-vl:2b")
        self.assertTrue(FakeOllama.posts[-1][1]["stream"])

    def test_a_name_ollama_does_not_know_is_reported_as_ollama_said_it(self):
        FakeOllama.pull_events = [{"error": "pull model manifest: file does not exist"}]
        ok, detail = setup.pull_model(self.url, "no-such-model", out=lambda *_: None)
        self.assertFalse(ok)
        self.assertEqual(detail, "pull model manifest: file does not exist")

    def test_a_download_that_just_stops_is_not_success(self):
        FakeOllama.pull_events = [{"status": "pulling manifest"}]
        ok, detail = setup.pull_model(self.url, "m", out=lambda *_: None)
        self.assertFalse(ok)
        self.assertIn("without Ollama reporting success", detail)

    def test_an_unreachable_ollama_is_reported(self):
        ok, detail = setup.pull_model("http://127.0.0.1:9", "m", out=lambda *_: None, read_timeout=2)
        self.assertFalse(ok)
        self.assertIn("not reachable", detail)

    def test_the_cli_needs_a_url_rather_than_guessing_one(self):
        with mock.patch("builtins.print") as printed:
            self.assertEqual(setup.main(["pull"]), 2)
        self.assertIn("QUALITY_VISION_URL is not set", str(printed.call_args_list))


class SmokeTest(SetupTestCase):
    def runner(self, **kwargs):
        return vision.Runner(provider=vision.OllamaProvider(url=self.url), models=["qwen3-vl:2b"],
                             coordination="off", cache=vision.Cache(self.dir / "cache"),
                             state_path=self.dir / "state.json", settle_seconds=0, **kwargs)

    def test_a_model_that_tells_match_from_mismatch_passes(self):
        FakeOllama.generate_replies = [dict(GOOD, adherence=5), dict(GOOD, adherence=1)]
        code, lines = setup.smoke(runner=self.runner())
        self.assertEqual(code, 0, lines)
        self.assertIn("smoke OK", lines[-1])
        self.assertIn("not a calibration", lines[-1])
        self.assertTrue(any(l.startswith("match") and "adherence 5/5" in l for l in lines))

    def test_a_model_that_cannot_tell_them_apart_fails(self):
        FakeOllama.generate_replies = [dict(GOOD, adherence=3), dict(GOOD, adherence=3)]
        code, lines = setup.smoke(runner=self.runner())
        self.assertEqual(code, 1)
        self.assertIn("smoke FAILED", lines[-1])

    def test_unusable_answers_are_incomplete_not_a_pass(self):
        FakeOllama.generate_replies = [{"adherence": 9}, {"adherence": 9}]
        code, lines = setup.smoke(runner=self.runner())
        self.assertEqual(code, 2)
        self.assertTrue(any("not judged" in l for l in lines))

    def test_without_a_configured_model_it_says_so(self):
        code, lines = setup.smoke(coordination="off")
        self.assertEqual(code, 2)
        self.assertIn("not ready", lines[0])

    def test_the_images_it_judges_really_differ(self):
        FakeOllama.generate_replies = [dict(GOOD), dict(GOOD)]
        setup.smoke(runner=self.runner())
        images = [p[1]["images"][0] for p in FakeOllama.posts if p[0] == "/api/generate"]
        self.assertEqual(len(images), 2)
        self.assertNotEqual(images[0], images[1])


class ServiceTest(SetupTestCase):
    def test_the_worker_unit_runs_the_agent_from_this_checkout_with_its_env(self):
        units = setup.service_units(python="/usr/bin/python3", root="/home/owner/content-machine-worker")
        text = units["content-machine-worker.service"]
        self.assertIn("WorkingDirectory=/home/owner/content-machine-worker", text)
        self.assertIn("EnvironmentFile=/home/owner/content-machine-worker/.env", text)
        self.assertIn("ExecStart=/usr/bin/python3 scripts/worker_agent.py", text)
        for section in ("[Unit]", "[Service]", "[Install]"):
            self.assertIn(section, text)
        self.assertNotIn("content-machine-tunnel.service", units)

    def test_the_tunnel_unit_dials_out_and_never_forwards_a_port_inward(self):
        units = setup.service_units(vps="root@vps.example")
        text = units["content-machine-tunnel.service"]
        self.assertIn("-L 8788:127.0.0.1:8788 root@vps.example", text)
        self.assertNotIn(" -R ", text, "a remote forward would expose this machine")
        self.assertIn("Restart=always", text)

    def test_write_puts_the_units_where_asked_and_nowhere_else(self):
        target = self.dir / "systemd"
        with mock.patch("builtins.print"):
            self.assertEqual(setup.main(["service", "--vps", "root@vps", "--write", str(target)]), 0)
        self.assertEqual(sorted(p.name for p in target.iterdir()),
                         ["content-machine-tunnel.service", "content-machine-worker.service"])
        self.assertIn(str(ROOT), (target / "content-machine-worker.service").read_text())

    def test_without_write_it_only_prints(self):
        with mock.patch("builtins.print") as printed:
            self.assertEqual(setup.main(["service"]), 0)
        self.assertIn("content-machine-worker.service", str(printed.call_args_list))


if __name__ == "__main__":
    unittest.main()

"""Media crosses machine identities; expired writers cannot publish files."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts import media, media_sync


class MediaSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pc = self.root / "pc/catalog.json"
        self.vps = self.root / "vps/catalog.json"
        self.original = self.root / "originals/photo.png"
        self.original.parent.mkdir()
        shutil.copy2(next((Path(__file__).parent / "fixtures/images").glob("*.png")), self.original)
        with patch.object(media, "host_id", return_value="a" * 64):
            media.scan(self.original, self.pc)
        self.identity = next(iter(media.load(self.pc)["assets"]))
        self.publish()

    def publish(self):
        return media_sync.inventory("pc-one", {"host_id": "a" * 64,
                                    "catalog": media.load(self.pc)}, self.vps)

    def test_transfer_verifies_and_stages_original_without_changing_it(self):
        before = self.original.stat().st_mtime_ns
        waiting = media_sync.request_transfer(self.identity, catalog=self.vps)
        self.assertEqual(waiting["attempt"], 0)
        self.assertIsNone(media_sync.claim("unrelated", "a" * 64, self.vps))
        job = media_sync.claim("pc-one", "a" * 64, self.vps)
        request = self.root / "request.json"
        media.atomic_json(request, job["request"])
        bundle = self.root / "transfer.tar.gz"
        with patch.object(media, "host_id", return_value="a" * 64):
            media.export_bundle(request, bundle, catalog=self.pc)
        finished = media_sync.receive(job["id"], "pc-one", job["lease_id"], bundle, self.vps)
        self.assertEqual(finished["state"], "READY")
        resolved = media.resolve(media.load(self.vps)["assets"][self.identity])
        self.assertEqual(media.digest(resolved), self.identity)
        self.assertNotEqual(resolved, self.original)
        self.assertEqual(self.original.stat().st_mtime_ns, before)
        self.assertIsNone(media.load(self.vps)["assets"][self.identity]["rights"])

    def test_expired_attempt_cannot_overwrite_new_attempt(self):
        pending = media_sync.request_transfer(self.identity, catalog=self.vps)
        first = media_sync.claim("pc-one", "a" * 64, self.vps)
        with patch.object(media_sync.time, "time", return_value=first["expires"] + 1):
            second = media_sync.claim("pc-one", "a" * 64, self.vps)
        self.assertNotEqual(first["lease_id"], second["lease_id"])
        with self.assertRaisesRegex(media.MediaError, "expired"):
            media_sync.fail(pending["id"], "pc-one", first["lease_id"], "late", self.vps)

    def test_duplicate_request_and_inventory_are_idempotent(self):
        self.publish()
        self.assertEqual(len(media.load(self.vps)["assets"]), 1)
        self.assertEqual(len(media.load(self.vps)["assets"][self.identity]["locations"]), 1)
        first = media_sync.request_transfer(self.identity, catalog=self.vps)
        self.assertEqual(first, media_sync.request_transfer(self.identity, catalog=self.vps))
        self.assertEqual(len([job for job in media_sync.transfers(self.vps) if job["request"]["mode"] == "source"]), 1)

    def test_missing_source_can_be_retried_without_losing_catalog(self):
        pending = media_sync.request_transfer(self.identity, catalog=self.vps)
        first = media_sync.claim("pc-one", "a" * 64, self.vps)
        media_sync.fail(pending["id"], "pc-one", first["lease_id"], "File is missing", self.vps)
        retried = media_sync.request_transfer(self.identity, catalog=self.vps)
        self.assertEqual(retried["state"], "WAITING")
        self.assertIn(self.identity, media.load(self.vps)["assets"])

    def test_inventory_cannot_claim_vps_paths(self):
        with self.assertRaises(media.MediaError):
            media_sync.inventory("pc-one", {"host_id": media.host_id(), "catalog": media.load(self.pc)}, self.vps)
        data = media.load(self.pc)
        data["assets"][self.identity]["locations"][0]["host_id"] = "b" * 64
        with self.assertRaises(media.MediaError):
            media_sync.inventory("pc-one", {"host_id": "a" * 64, "catalog": data}, self.vps)

    def test_wrong_worker_and_bad_bundle_leave_source_unavailable(self):
        pending = media_sync.request_transfer(self.identity, catalog=self.vps)
        first = media_sync.claim("pc-one", "a" * 64, self.vps)
        with self.assertRaises(media.MediaError):
            media_sync.receive(pending["id"], "other", first["lease_id"], self.original, self.vps)
        with self.assertRaises(media.MediaError):
            media.resolve(media.load(self.vps)["assets"][self.identity])

    def test_real_http_transport_lands_verified_source(self):
        from http.server import ThreadingHTTPServer
        import threading
        import urllib.request
        from scripts import worker_api
        with patch.object(worker_api.worker, "authenticate", return_value={"worker_id": "pc-one"}), patch.object(worker_api.worker, "reap"), patch.object(worker_api.media, "DEFAULT_CATALOG", self.vps):
            server = ThreadingHTTPServer(("127.0.0.1", 0), worker_api.ControlPlaneHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def post(path, data, headers=None):
                    request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}",
                        data=data, headers={"Authorization": "Bearer fixture", **(headers or {})})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return json.load(response)
                media_sync.request_transfer(self.identity, catalog=self.vps)
                job = post("/v1/media/claim", json.dumps({"host_id": "a" * 64}).encode())["transfer"]
                request = self.root / "http-request.json"
                bundle = self.root / "http-bundle.tar.gz"
                media.atomic_json(request, job["request"])
                with patch.object(media, "host_id", return_value="a" * 64):
                    media.export_bundle(request, bundle, catalog=self.pc)
                with bundle.open("rb") as stream:
                    result = post(f"/v1/media/{job['id']}/bundle", stream,
                                  {"X-Lease-Id": job["lease_id"], "Content-Length": str(bundle.stat().st_size)})
                self.assertEqual(result["state"], "READY")
                self.assertEqual(media.digest(media.resolve(media.load(self.vps)["assets"][self.identity])), self.identity)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

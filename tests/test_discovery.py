"""Openverse licensing and imports; mocked HTTP, actual media inspection."""
import copy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import discovery
import media


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cm-discovery-test-")
        self.addCleanup(temporary.cleanup)
        self.catalog = Path(temporary.name) / "library" / "catalog.json"
        self.identity = "12345678-1234-1234-1234-123456789abc"
        self.record = {
            "id": self.identity, "title": "Moonlit forest", "creator": "Fixture creator",
            "foreign_landing_url": "https://commons.wikimedia.org/wiki/File:Fixture.png",
            "url": "https://upload.wikimedia.org/fixture.png",
            "license": "by", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "Moonlit forest by Fixture creator, CC BY 4.0",
            "source": "wikimedia", "tags": [{"name": "forest"}, {"invalid": "ignored"}],
        }
        # Every HTTP entry point is intercepted, even if an implementation changes.
        self.http = self.enterContext(patch.object(discovery.urllib.request, "urlopen"))
        self.opener = Mock()
        self.enterContext(patch.object(discovery.urllib.request, "build_opener", return_value=self.opener))
        self.http.side_effect = lambda *a, **kw: io.BytesIO(json.dumps(self.record).encode())
        self.body = (ROOT / "tests/fixtures/images/01_red.png").read_bytes()
        self.opener.open.side_effect = lambda *a, **kw: io.BytesIO(self.body)

    def test_search_requests_and_returns_only_supported_commercial_licenses(self):
        records = [dict(self.record, id=str(i), license=license_name)
                   for i, license_name in enumerate(("cc0", "pdm", "by", "by-nc", "by-nd", "unknown", None))]
        self.http.side_effect = lambda *a, **kw: io.BytesIO(json.dumps({"results": records}).encode())
        results = discovery.search("quiet moonlit forest", "image")
        self.assertEqual([item["license"] for item in results], ["cc0", "pdm", "by"])
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.http.call_args.args[0].full_url).query)
        self.assertEqual(query["q"], ["quiet moonlit forest"])
        self.assertEqual(query["license"], ["cc0,pdm,by"])
        self.assertEqual(query["mature"], ["false"])

    def test_search_failure_is_actionable(self):
        self.http.side_effect = urllib.error.URLError("offline")
        with self.assertRaisesRegex(media.MediaError, "temporarily unavailable"):
            discovery.search("forest")

    def test_import_rechecks_license_and_refuses_incomplete_evidence_before_download(self):
        for fields in ({"license": "by-nc"}, {"license": "unknown"}, {"license": None},
                       {"license_url": ""}, {"foreign_landing_url": ""}):
            with self.subTest(fields=fields):
                record = dict(self.record, **fields)
                self.http.side_effect = lambda *a, **kw: io.BytesIO(json.dumps(record).encode())
                with self.assertRaisesRegex(media.MediaError, "import refused"):
                    discovery.import_asset(self.identity, catalog=self.catalog)
        self.opener.open.assert_not_called()
        self.assertFalse(self.catalog.exists())

    def test_unsafe_downloads_are_rejected_before_fetch(self):
        for url in ("http://upload.wikimedia.org/a.png", "https://localhost/a.png",
                    "https://127.0.0.1/a.png", "file:///etc/passwd",
                    "https://wikimedia.org.attacker.example/a.png",
                    "https://evilwikimedia.org/a.png", "https://user@wikimedia.org/a.png",
                    "https://wikimedia.org:8188/a.png", "https://wikimedia.org:bad/a.png",
                    "https://[broken/a.png"):
            with self.subTest(url=url):
                self.record["url"] = url
                with self.assertRaises(media.MediaError):
                    discovery.import_asset(self.identity, catalog=self.catalog)
        self.opener.open.assert_not_called()

    def test_redirects_reapply_source_host_and_https_restrictions(self):
        handler = discovery._SafeRedirect()
        request = urllib.request.Request("https://flickr.com/photo.png")
        safe = handler.redirect_request(request, None, 302, "Found", {},
                                        "https://live.staticflickr.com/photo.png")
        self.assertEqual(safe.full_url, "https://live.staticflickr.com/photo.png")
        for destination in ("http://flickr.com/photo.png", "https://127.0.0.1/private",
                            "https://flickr.com.attacker.example/private"):
            with self.subTest(destination=destination), self.assertRaises(media.MediaError):
                handler.redirect_request(request, None, 302, "Found", {}, destination)

    def test_invalid_identity_and_kind_never_reach_network(self):
        for identity, kind in (("../../private", "image"), (self.identity, "video")):
            with self.subTest(identity=identity, kind=kind), self.assertRaises(media.MediaError):
                discovery.import_asset(identity, kind, catalog=self.catalog)
        self.http.assert_not_called()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "media tools required")
    def test_actual_image_import_preserves_source_and_blocks_unreviewed_use(self):
        asset = discovery.import_asset(self.identity, catalog=self.catalog)
        self.assertEqual(asset["technical"]["inspection"], "full_decode")
        self.assertEqual(asset["technical"]["kind"], "image")
        self.assertEqual(asset["source"], self.record["foreign_landing_url"])
        self.assertEqual(asset["provenance"]["creator"], self.record["creator"])
        self.assertEqual(asset["provenance"]["external_id"], self.identity)
        self.assertEqual(asset["rights"]["status"], "NEEDS_SOURCE_REVIEW")
        self.assertEqual(asset["rights"]["license"], self.record["license_url"])
        self.assertEqual(asset["rights"]["attribution_text"], self.record["attribution"])
        self.assertTrue(asset["rights"]["attribution_required"])
        self.assertFalse(asset["rights"]["commercial_use"])
        self.assertEqual(media.resolve(asset).read_bytes(), self.body)
        self.assertEqual(media.load(self.catalog)["assets"][asset["id"]], asset)
        with self.assertRaisesRegex(media.MediaError, "rights and evidence"):
            media.select(asset["id"], kind="image", catalog=self.catalog)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "media tools required")
    def test_cc0_and_public_domain_audio_import_still_require_source_review(self):
        self.body = (ROOT / "tests/fixtures/audio/test_tone.wav").read_bytes()
        self.record["url"] = "https://freesound.org/fixture.wav"
        for license_name in ("cc0", "pdm"):
            with self.subTest(license_name=license_name):
                catalog = self.catalog.parent / license_name / "catalog.json"
                self.record["license"] = license_name
                asset = discovery.import_asset(self.identity, "audio", catalog=catalog)
                self.assertIn("/audio/", self.http.call_args.args[0].full_url)
                self.assertEqual(asset["technical"]["kind"], "audio")
                self.assertFalse(asset["rights"]["attribution_required"])
                self.assertEqual(asset["rights"]["status"], "NEEDS_SOURCE_REVIEW")
                with self.assertRaises(media.MediaError):
                    media.select(asset["id"], kind="audio", catalog=catalog)

    def test_interrupted_download_keeps_existing_catalog_and_cleans_staging(self):
        media.atomic_json(self.catalog, {"version": 1, "assets": {}, "issues": {}})
        original = self.catalog.read_bytes()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [self.body[:100], OSError("connection lost")]
        self.opener.open.side_effect = None
        self.opener.open.return_value = response
        with self.assertRaisesRegex(media.MediaError, "Nothing was imported"):
            discovery.import_asset(self.identity, catalog=self.catalog)
        self.assertEqual(self.catalog.read_bytes(), original)
        self.assertEqual(list((self.catalog.parent / "objects").iterdir()), [])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "media tools required")
    def test_wrong_media_kind_leaves_catalog_unchanged(self):
        with self.assertRaisesRegex(media.MediaError, "does not match"):
            discovery.import_asset(self.identity, "audio", catalog=self.catalog)
        self.assertFalse(self.catalog.exists())
        self.assertEqual(list((self.catalog.parent / "objects").iterdir()), [])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "media tools required")
    def test_duplicates_preserve_creator_annotations_and_do_not_duplicate_files(self):
        original = discovery.import_asset(self.identity, catalog=self.catalog)
        annotated = copy.deepcopy(original)
        annotated["description"] = "My approved direction"
        annotated["rights"]["commercial_use"] = True
        annotated["rights"]["status"] = "VERIFIED"
        media.atomic_json(self.catalog, {"version": 1, "assets": {original["id"]: annotated}, "issues": {}})
        again = discovery.import_asset(self.identity, catalog=self.catalog)
        self.assertEqual(again, annotated)
        self.assertEqual(len(media.load(self.catalog)["assets"]), 1)
        self.assertEqual(len(again["locations"]), 1)
        self.assertEqual(len(list((self.catalog.parent / "objects").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()

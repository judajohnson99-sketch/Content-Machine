#!/usr/bin/env python3
"""Stock discovery: vendor responses -> candidates, and every way a provider
can fail without taking the others down.

No network. A scripted transport stands in for each vendor and returns the
documented response shapes, so these tests defend the behaviour that matters:
rate limits are honoured and remembered, an outage degrades to the remaining
providers, results are cached and deduplicated, and a download can only come
from the provider's own host.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import asset_licensing as licensing  # noqa: E402
import stock_providers as stock  # noqa: E402

PEXELS_PHOTOS = {
    "page": 1, "per_page": 15, "total_results": 40,
    "next_page": "https://api.pexels.com/v1/search?page=2",
    "photos": [{
        "id": 101, "width": 4000, "height": 2250,
        "url": "https://www.pexels.com/photo/misty-forest-101/",
        "photographer": "Ada Ng", "photographer_url": "https://www.pexels.com/@ada",
        "alt": "Misty pine forest at dawn",
        "src": {"original": "https://images.pexels.com/photos/101/original.jpg",
                "large2x": "https://images.pexels.com/photos/101/large2x.jpg",
                "medium": "https://images.pexels.com/photos/101/medium.jpg"},
    }],
}
PEXELS_VIDEOS = {
    "page": 1, "per_page": 15, "total_results": 3, "videos": [{
        "id": 202, "width": 3840, "height": 2160, "duration": 14,
        "url": "https://www.pexels.com/video/fog-rolling-over-hills-202/",
        "image": "https://images.pexels.com/videos/202/preview.jpg",
        "user": {"id": 9, "name": "Bo Lind", "url": "https://www.pexels.com/@bo"},
        "video_files": [
            {"id": 1, "quality": "sd", "file_type": "video/mp4", "width": 960,
             "height": 540, "fps": 25, "link": "https://videos.pexels.com/202/sd.mp4"},
            {"id": 2, "quality": "hd", "file_type": "video/mp4", "width": 1920,
             "height": 1080, "fps": 25, "link": "https://videos.pexels.com/202/hd.mp4"},
            {"id": 3, "quality": "uhd", "file_type": "video/mp4", "width": 3840,
             "height": 2160, "fps": 25, "link": "https://videos.pexels.com/202/uhd.mp4"},
        ]}],
}
PIXABAY_PHOTOS = {
    "total": 500, "totalHits": 100, "hits": [{
        "id": 303, "pageURL": "https://pixabay.com/photos/forest-fog-303/",
        "type": "photo", "tags": "forest, fog, trees",
        "previewURL": "https://cdn.pixabay.com/photo/303_150.jpg",
        "webformatURL": "https://pixabay.com/get/303_640.jpg",
        "largeImageURL": "https://pixabay.com/get/303_1280.jpg",
        "imageWidth": 6000, "imageHeight": 4000, "user": "carla", "user_id": 77}],
}
PIXABAY_VIDEOS = {
    "total": 10, "totalHits": 10, "hits": [{
        "id": 404, "pageURL": "https://pixabay.com/videos/river-404/",
        "type": "film", "tags": "river, stream, nature", "duration": 20,
        "videos": {
            "large": {"url": "https://cdn.pixabay.com/video/404_large.mp4",
                      "width": 1920, "height": 1080, "size": 1000, "thumbnail": "https://cdn.pixabay.com/video/404_l.jpg"},
            "medium": {"url": "https://cdn.pixabay.com/video/404_med.mp4",
                       "width": 1280, "height": 720, "size": 500, "thumbnail": ""}},
        "user": "dmitri", "user_id": 88}],
}
UNSPLASH_PHOTOS = {
    "total": 30, "total_pages": 3, "results": [{
        "id": "u1", "width": 5000, "height": 3000, "description": "Quiet lake at night",
        "alt_description": "calm water under stars", "tags": [{"title": "lake"}, {"title": "night"}],
        "urls": {"raw": "https://images.unsplash.com/photo-u1?ixid=abc", "small": "https://images.unsplash.com/photo-u1?w=400"},
        "links": {"html": "https://unsplash.com/photos/u1",
                  "download_location": "https://api.unsplash.com/photos/u1/download?ixid=abc"},
        "user": {"name": "Eli Tran", "links": {"html": "https://unsplash.com/@eli"}}}],
}


class Scripted:
    """A transport that answers by URL substring, counting calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers=None, timeout=20):
        self.calls.append((url, dict(headers or {})))
        for needle, answer in self.routes:
            if needle in url:
                result = answer(url) if callable(answer) else answer
                if isinstance(result, Exception):
                    raise result
                status, body, hdrs = result
                return stock.Response(status, hdrs, json.dumps(body).encode())
        return stock.Response(404, {}, b"{}")


def ok(body, **headers):
    return (200, body, headers)


class StockCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1_000_000.0]
        env = {"PEXELS_API_KEY": "pk", "PIXABAY_API_KEY": "xk", "UNSPLASH_ACCESS_KEY": "uk"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def hub(self, routes, providers=None):
        self.transport = Scripted(routes)
        return stock.StockHub(
            providers=providers or [stock.Pexels(), stock.Pixabay(), stock.Unsplash()],
            directory=self.tmp.name, transport=self.transport,
            clock=lambda: self.now[0], sleep=lambda s: None)


class AdapterTests(StockCase):
    def test_every_provider_yields_the_same_candidate_shape(self):
        hub = self.hub([("api.pexels.com/v1", ok(PEXELS_PHOTOS)),
                        ("pixabay.com/api/?", ok(PIXABAY_PHOTOS)),
                        ("api.unsplash.com/search", ok(UNSPLASH_PHOTOS))])
        result = hub.search("misty forest", kind="image")
        by_provider = {c["provider"]: c for c in result["candidates"]}
        self.assertEqual(set(by_provider), {"pexels", "pixabay", "unsplash"})
        for candidate in by_provider.values():
            for key in ("candidate_id", "source", "media_kind", "width", "height", "preview",
                        "full", "creator", "source_url", "rights", "cost", "state"):
                self.assertIn(key, candidate)
            self.assertEqual(candidate["source"], "stock")
            self.assertEqual(candidate["cost"]["money_usd"], 0.0)
            self.assertTrue(candidate["preview"]["url"])
        pexels = by_provider["pexels"]
        self.assertEqual(pexels["creator"]["name"], "Ada Ng")
        self.assertEqual(pexels["orientation"], "landscape")
        self.assertEqual(pexels["full"]["width"], 1880, "delivered at large2x, not the 4000px original")
        self.assertEqual(pexels["variants"][0]["label"], "original")

    def test_credentials_are_sent_as_the_provider_requires_and_never_cached_on_disk(self):
        hub = self.hub([("api.pexels.com/v1", ok(PEXELS_PHOTOS)),
                        ("pixabay.com/api/?", ok(PIXABAY_PHOTOS)),
                        ("api.unsplash.com/search", ok(UNSPLASH_PHOTOS))])
        hub.search("forest", kind="image")
        sent = {url.split("/")[2]: headers for url, headers in self.transport.calls}
        self.assertEqual(sent["api.pexels.com"]["Authorization"], "pk")
        self.assertEqual(sent["api.unsplash.com"]["Authorization"], "Client-ID uk")
        on_disk = "".join(p.read_text() for p in Path(self.tmp.name).rglob("*.json"))
        self.assertNotIn("xk", on_disk, "the Pixabay key is in the query string; it must not be cached")
        self.assertNotIn('"pk"', on_disk)

    def test_pexels_video_picks_the_smallest_file_that_covers_1080p(self):
        hub = self.hub([("api.pexels.com/videos", ok(PEXELS_VIDEOS))], [stock.Pexels()])
        video = hub.search("fog hills", kind="video")["candidates"][0]
        self.assertEqual(video["media_kind"], "video")
        self.assertEqual(video["full"]["width"], 1920)
        self.assertEqual(video["duration_seconds"], 14)
        self.assertEqual({v["label"] for v in video["variants"]}, {"sd", "uhd"})

    def test_pixabay_video_and_illustration_search(self):
        hub = self.hub([("pixabay.com/api/videos", ok(PIXABAY_VIDEOS))], [stock.Pixabay()])
        video = hub.search("river", kind="video")["candidates"][0]
        self.assertEqual(video["full"]["width"], 1920)
        self.assertEqual(video["creator"]["url"], "https://pixabay.com/users/dmitri-88/")
        self.assertIn("stream", video["tags"])

    def test_unsplash_has_no_video_and_says_so(self):
        hub = self.hub([], [stock.Unsplash()])
        result = hub.search("anything", kind="video")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["providers"][0]["state"], "unsupported")
        self.assertEqual(self.transport.calls, [], "no request is made for something it cannot do")

    def test_unsplash_credit_carries_the_required_referral_params(self):
        hub = self.hub([("api.unsplash.com/search", ok(UNSPLASH_PHOTOS))], [stock.Unsplash()])
        candidate = hub.search("lake", kind="image")["candidates"][0]
        self.assertIn("utm_source=content_machine", candidate["source_url"])
        self.assertIn("utm_source=content_machine", candidate["creator"]["url"])
        self.assertIn("Eli Tran", candidate["rights"]["attribution_text"])
        self.assertTrue(candidate["rights"]["attribution_required"])

    def test_pagination_reports_the_next_page_and_continues_from_it(self):
        hub = self.hub([("api.pexels.com/v1", ok(PEXELS_PHOTOS))], [stock.Pexels()])
        first = hub.search("forest", kind="image")
        self.assertEqual(first["next_pages"], {"pexels": 2})
        hub.search("forest", kind="image", pages=first["next_pages"])
        self.assertIn("page=2", self.transport.calls[-1][0])

    def test_missing_keys_are_not_configured_not_errors(self):
        with mock.patch.dict(os.environ, {"PEXELS_API_KEY": ""}):
            hub = self.hub([("pixabay.com/api/?", ok(PIXABAY_PHOTOS))], [stock.Pexels(), stock.Pixabay()])
            result = hub.search("forest", kind="image")
        states = {p["provider"]: p["state"] for p in result["providers"]}
        self.assertEqual(states, {"pexels": "not_configured", "pixabay": "ready"})
        self.assertEqual(len(result["candidates"]), 1)


class FailureTests(StockCase):
    def test_one_provider_down_does_not_stop_the_others(self):
        hub = self.hub([("api.pexels.com/v1", (503, {}, {})),
                        ("pixabay.com/api/?", ok(PIXABAY_PHOTOS))],
                       [stock.Pexels(), stock.Pixabay()])
        result = hub.search("forest", kind="image")
        states = {p["provider"]: p["state"] for p in result["providers"]}
        self.assertEqual(states["pexels"], "unavailable")
        self.assertEqual(states["pixabay"], "ready")
        self.assertEqual([c["provider"] for c in result["candidates"]], ["pixabay"])

    def test_a_transient_outage_is_retried_once_then_cooled_down(self):
        answers = iter([(500, {}, {}), ok(PEXELS_PHOTOS)])
        hub = self.hub([("api.pexels.com/v1", lambda url: next(answers))], [stock.Pexels()])
        result = hub.search("forest", kind="image")
        self.assertEqual(len(result["candidates"]), 1, "the retry succeeded")
        self.assertEqual(len(self.transport.calls), 2)

        down = self.hub([("api.pexels.com/v1", (500, {}, {}))], [stock.Pexels()])
        self.assertEqual(down.search("other words", kind="image")["providers"][0]["state"], "unavailable")
        calls = len(self.transport.calls)
        again = down.search("yet other words", kind="image")
        self.assertEqual(again["providers"][0]["state"], "unavailable")
        self.assertEqual(len(self.transport.calls), calls, "a provider in cooldown is not hammered")

    def test_network_errors_are_an_outage_not_a_crash(self):
        hub = self.hub([("api.pexels.com/v1", stock.StockError("network error: timed out"))], [stock.Pexels()])
        self.assertEqual(hub.search("forest", kind="image")["providers"][0]["state"], "unavailable")

    def test_a_429_starts_a_cooldown_that_outlives_the_process(self):
        hub = self.hub([("api.pexels.com/v1", (429, {}, {"Retry-After": "120"}))], [stock.Pexels()])
        first = hub.search("forest", kind="image")
        self.assertEqual(first["providers"][0]["state"], "rate_limited")
        calls = len(self.transport.calls)

        # A new process: same directory, fresh objects. The cooldown is on disk.
        later = self.hub([("api.pexels.com/v1", ok(PEXELS_PHOTOS))], [stock.Pexels()])
        blocked = later.search("river", kind="image")
        self.assertEqual(blocked["providers"][0]["state"], "rate_limited")
        self.assertEqual(self.transport.calls, [], "no request while cooling down")
        self.assertEqual(calls, 1)

        self.now[0] += 121
        recovered = later.search("river", kind="image")
        self.assertEqual(recovered["providers"][0]["state"], "ready")
        self.assertEqual(len(recovered["candidates"]), 1)

    def test_an_exhausted_quota_header_stops_further_requests_before_a_429(self):
        hub = self.hub([("api.pexels.com/v1", ok(PEXELS_PHOTOS, **{"X-Ratelimit-Remaining": "0",
                                                                    "X-Ratelimit-Reset": "600"}))],
                       [stock.Pexels()])
        self.assertEqual(len(hub.search("forest", kind="image")["candidates"]), 1,
                         "the response that exhausted the quota is still used")
        status = hub.provider_status(hub.providers[0])
        self.assertEqual(status["state"], "rate_limited")
        self.assertGreater(status["cooldown_seconds"], 0)

    def test_a_rate_limited_provider_serves_its_earlier_results_rather_than_nothing(self):
        responses = iter([ok(PEXELS_PHOTOS), (429, {}, {"Retry-After": "60"})])
        hub = self.hub([("api.pexels.com/v1", lambda url: next(responses))], [stock.Pexels()])
        hub.search("forest", kind="image")
        self.now[0] += stock.CACHE_TTL_SECONDS + 5      # cache entry is now stale
        result = hub.search("forest", kind="image")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["providers"][0]["from"], "stale-cache")
        self.assertIn("rate limited", result["providers"][0]["detail"])

    def test_a_rejected_key_is_reported_and_not_retried_for_an_hour(self):
        hub = self.hub([("api.pexels.com/v1", (401, {}, {}))], [stock.Pexels()])
        self.assertEqual(hub.search("forest", kind="image")["providers"][0]["state"], "auth_failed")
        self.now[0] += 600
        hub.search("rivers", kind="image")
        self.assertEqual(len(self.transport.calls), 1)

    def test_unreadable_data_is_an_outage_not_an_exception(self):
        hub = self.hub([("api.pexels.com/v1", lambda url: (200, "not json", {}))], [stock.Pexels()])
        hub.transport = lambda url, headers=None, timeout=20: stock.Response(200, {}, b"<html>")
        self.assertEqual(hub.search("forest", kind="image")["providers"][0]["state"], "unavailable")


class CacheAndDedupeTests(StockCase):
    def test_a_repeat_search_within_a_day_is_served_from_cache(self):
        hub = self.hub([("api.pexels.com/v1", ok(PEXELS_PHOTOS))], [stock.Pexels()])
        hub.search("forest", kind="image")
        again = hub.search("forest", kind="image")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(again["providers"][0]["from"], "cache")
        self.now[0] += stock.CACHE_TTL_SECONDS + 1
        hub.search("forest", kind="image")
        self.assertEqual(len(self.transport.calls), 2, "the cache expires after 24 hours")

    def test_the_same_photo_mirrored_across_providers_appears_once(self):
        mirrored = json.loads(json.dumps(PIXABAY_PHOTOS))
        mirrored["hits"][0].update(user="Ada Ng", imageWidth=4000, imageHeight=2250)
        hub = self.hub([("api.pexels.com/v1", ok(PEXELS_PHOTOS)), ("pixabay.com/api/?", ok(mirrored))],
                       [stock.Pexels(), stock.Pixabay()])
        providers = [c["provider"] for c in hub.search("forest", kind="image")["candidates"]]
        self.assertEqual(providers, ["pexels"], "same creator and pixel size is one picture")

    def test_dedupe_keeps_distinct_pictures_and_drops_exact_repeats(self):
        a = {"candidate_id": "stock:p:image:1", "creator": {"name": "x"}, "width": 10,
             "height": 5, "duration_seconds": None, "media_kind": "image"}
        b = dict(a, candidate_id="stock:p:image:2", width=11)
        self.assertEqual(len(stock.dedupe([a, dict(a), b])), 2)


class DownloadTests(StockCase):
    def candidate(self, url, provider="pexels"):
        return {"provider": provider, "external_id": "9", "media_kind": "image",
                "full": {"url": url}}

    def test_a_download_url_outside_the_providers_hosts_is_refused(self):
        hub = self.hub([])
        for url in ("https://evil.example.com/a.jpg",
                    "http://images.pexels.com/a.jpg",
                    "https://images.pexels.com.evil.example.com/a.jpg",
                    "https://user:pw@images.pexels.com/a.jpg",
                    "https://images.pexels.com:8443/a.jpg"):
            with self.assertRaises(stock.StockError) as caught:
                hub.download(self.candidate(url), self.tmp.name)
            self.assertEqual(caught.exception.state, "unsafe", url)

    def test_a_rate_limited_provider_refuses_downloads_too(self):
        hub = self.hub([("api.pexels.com/v1", (429, {}, {"Retry-After": "300"}))], [stock.Pexels()])
        hub.search("forest", kind="image")
        with self.assertRaises(stock.StockError) as caught:
            hub.download(self.candidate("https://images.pexels.com/a.jpg"), self.tmp.name)
        self.assertEqual(caught.exception.state, "rate_limited")

    def test_unsplash_download_is_reported_to_unsplash_first(self):
        hub = self.hub([("download?ixid", ok({"url": "https://images.unsplash.com/final.jpg"}))],
                       [stock.Unsplash()])
        candidate = {"provider": "unsplash", "external_id": "u1", "media_kind": "image",
                     "download_location": "https://api.unsplash.com/photos/u1/download?ixid=abc",
                     "full": {"url": "https://images.unsplash.com/fallback.jpg"}}
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=OSError("offline")):
            with self.assertRaises(stock.StockError):
                hub.download(candidate, self.tmp.name)
        self.assertTrue(any("download?ixid" in url for url, _ in self.transport.calls),
                        "the tracking endpoint was called before fetching")


class HostileResponseTests(StockCase):
    def test_non_https_links_from_a_provider_never_reach_the_browser(self):
        hostile = json.loads(json.dumps(PEXELS_PHOTOS))
        photo = hostile["photos"][0]
        photo["url"] = "javascript:alert(1)"
        photo["photographer_url"] = "data:text/html,<script>"
        photo["src"]["medium"] = "http://images.pexels.com/insecure.jpg"
        hub = self.hub([("api.pexels.com/v1", ok(hostile))], [stock.Pexels()])
        candidate = hub.search("forest", kind="image")["candidates"][0]
        self.assertIsNone(candidate["source_url"])
        self.assertIsNone(candidate["creator"]["url"])
        self.assertIsNone(candidate["preview"]["url"])
        self.assertEqual(candidate["rights"]["eligibility"], licensing.REVIEW_REQUIRED,
                         "no source page to cite means a person reviews the rights")

    def test_an_external_id_cannot_steer_where_a_download_is_written(self):
        class Body:
            def __init__(self):
                self.chunks = [b"bytes", b""]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, size=-1):
                return self.chunks.pop(0)

        hub = self.hub([])
        candidate = {"provider": "pexels", "external_id": "../../etc/passwd", "media_kind": "image",
                     "full": {"url": "https://images.pexels.com/a.jpg"}}
        target_dir = Path(self.tmp.name) / "downloads"
        with mock.patch.object(stock.urllib.request.OpenerDirector, "open",
                               lambda self_, request, timeout=None: Body()):
            path = hub.download(candidate, target_dir)
        self.assertEqual(path.parent, target_dir)
        self.assertEqual(path.name, "pexels-______etc_passwd.jpg")
        self.assertEqual(path.read_bytes(), b"bytes")


class RightsTests(StockCase):
    def test_a_stock_candidate_is_never_pre_approved(self):
        hub = self.hub([("api.pexels.com/v1", ok(PEXELS_PHOTOS))], [stock.Pexels()])
        rights = hub.search("forest", kind="image")["candidates"][0]["rights"]
        self.assertIs(rights["commercial_use"], False, "the provider's terms are the lead, not clearance")
        self.assertIs(rights["provider_terms"]["commercial_use"], True)
        self.assertEqual(rights["eligibility"], licensing.ELIGIBLE)
        self.assertEqual(rights["status"], licensing.PENDING)
        self.assertEqual(rights["license_url"], "https://www.pexels.com/license/")
        self.assertTrue(rights["retrieved_utc"])
        self.assertTrue(rights["provider_terms"]["restrictions"])
        self.assertTrue(rights["external"])

    def test_a_missing_creator_makes_the_record_need_review(self):
        broken = json.loads(json.dumps(PEXELS_PHOTOS))
        broken["photos"][0]["photographer"] = ""
        hub = self.hub([("api.pexels.com/v1", ok(broken))], [stock.Pexels()])
        rights = hub.search("forest", kind="image")["candidates"][0]["rights"]
        self.assertEqual(rights["eligibility"], licensing.REVIEW_REQUIRED)
        self.assertIn("creator", " ".join(rights["eligibility_reasons"]))


if __name__ == "__main__":
    unittest.main()

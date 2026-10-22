"""DRF endpoint tests for the owner's media library: request/response
contract only. The service layer is mocked, so these need no catalog, no
files and no ffmpeg - what "selectable" means and what a selection does are
proven in the main repo's tests/test_ownermedia.py (architecture plan §17).
"""
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

ASSET_ID = "a" * 64


def asset(**overrides):
    base = {
        "id": ASSET_ID, "kind": "image", "origin": "owner",
        "description": "Rain on a window", "source": "Shot by the owner",
        "tags": ["rain"], "technical": {"width": 1920, "height": 1080},
        "rights": {"license": "Owner's own work", "commercial_use": True},
        "available": True, "resolved_path": "/home/owner/rain.png",
        "selectable": True, "problems": [], "roles": ["visuals"],
    }
    base.update(overrides)
    return base


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(username="owner", password="pw")


@pytest.fixture
def client(owner):
    api = APIClient()
    api.force_authenticate(user=owner)
    return api


@pytest.mark.django_db
class TestLibraryListView:
    def test_connections_and_analysis_are_served_through_domain(self, client):
        with patch("apps.library.views.library_service.connections", return_value={"sources": [], "transfers": []}) as connections:
            assert client.get("/api/v1/library/connections/").status_code == 200
            connections.assert_called_once()
        with patch("apps.library.views.library_service.analyze", return_value={"id": ASSET_ID}) as analyze:
            assert client.post(f"/api/v1/library/{ASSET_ID}/analyze/", {}, format="json").status_code == 200
            analyze.assert_called_once_with(ASSET_ID)

    def test_retrieval_preserves_domain_wait_state(self, client):
        with patch("apps.library.views.library_service.retrieve", return_value={"state": "WAITING"}) as retrieve:
            response = client.post(f"/api/v1/library/{ASSET_ID}/retrieve/", {"mode": "source"}, format="json")
            assert response.status_code == 202
            assert response.data["state"] == "WAITING"
            retrieve.assert_called_once_with(ASSET_ID, "source")

    def test_discovery_and_contextual_suggestions_are_inspectable(self, client):
        with patch("apps.library.views.library_service.discover", return_value=[]) as discover:
            assert client.get("/api/v1/library/discover/?query=forest&kind=image").data == {"assets": []}
            discover.assert_called_once_with("forest", "image")
        with patch("apps.library.views.library_service.suggest", return_value={"query": "moonlit forest", "assets": []}) as suggest:
            response = client.get("/api/v1/library/suggestions/?project=demo&role=visuals")
            assert response.data["query"] == "moonlit forest"
            suggest.assert_called_once_with("demo", "visuals")
    def test_returns_the_catalog_with_the_roles_it_can_fill(self, client):
        with patch("apps.library.views.library_service.browse",
                   return_value=[asset()]) as call:
            response = client.get("/api/v1/library/?kind=image&query=rain")
        assert response.status_code == 200
        body = response.json()
        assert body["assets"] == [asset()]
        assert [r["role"] for r in body["roles"]] == ["visuals", "music", "ambience", "sfx"]
        call.assert_called_once_with(query="rain", kind="image")

    def test_an_unsupported_kind_is_a_400(self, client):
        assert client.get("/api/v1/library/?kind=hologram").status_code == 400

    def test_unauthenticated_request_is_rejected(self):
        assert APIClient().get("/api/v1/library/").status_code == 403


@pytest.mark.django_db
class TestLibraryAssetView:
    def test_unknown_asset_is_a_404(self, client):
        with patch("apps.library.views.library_service.describe", return_value=None):
            assert client.get(f"/api/v1/library/{ASSET_ID}/").status_code == 404

    def test_annotation_records_the_owners_own_claims(self, client):
        body = {
            "description": "Rain on a window at night",
            "source": "Shot by the owner, 2026-04",
            "tags": ["rain", "night"],
            "rights": {"source": "Me", "license": "My own work",
                       "commercial_use": True, "evidence": "I filmed it"},
        }
        with patch("apps.library.views.library_service.annotate") as call, \
                patch("apps.library.views.library_service.describe", return_value=asset()):
            response = client.put(f"/api/v1/library/{ASSET_ID}/", body, format="json")
        assert response.status_code == 200
        kwargs = call.call_args.kwargs
        assert kwargs["description"] == body["description"]
        assert kwargs["rights"]["commercial_use"] is True
        assert kwargs["origin"] == "owner"

    def test_rights_are_all_or_nothing_not_half_a_claim(self, client):
        response = client.put(f"/api/v1/library/{ASSET_ID}/", {
            "description": "A still", "source": "Me",
            "rights": {"license": "My own work"},
        }, format="json")
        assert response.status_code == 400
        assert set(response.json()["rights"]) == {"source", "commercial_use", "evidence"}

    def test_an_asset_may_be_described_without_settling_its_rights(self, client):
        with patch("apps.library.views.library_service.annotate") as call, \
                patch("apps.library.views.library_service.describe",
                      return_value=asset(selectable=False, rights=None,
                                         problems=["needs a rights record"])):
            response = client.put(f"/api/v1/library/{ASSET_ID}/", {
                "description": "A still", "source": "Me"}, format="json")
        assert response.status_code == 200
        assert call.call_args.kwargs["rights"] is None
        assert response.json()["selectable"] is False


@pytest.mark.django_db
class TestLibraryScanView:
    def test_scans_the_given_path(self, client):
        report = {"indexed": 3, "issues": []}
        with patch("apps.library.views.library_service.scan", return_value=report) as call:
            response = client.post("/api/v1/library/scan/",
                                   {"path": "/home/owner/footage"}, format="json")
        assert response.status_code == 200
        assert response.json() == report
        call.assert_called_once_with("/home/owner/footage")

    def test_a_path_that_cannot_be_read_is_a_400_with_the_reason(self, client):
        import scripts.media as media
        with patch("apps.library.views.library_service.scan",
                   side_effect=media.MediaError("No such file or directory")):
            response = client.post("/api/v1/library/scan/", {"path": "/nope"}, format="json")
        assert response.status_code == 400
        assert "No such file" in response.json()["detail"]


@pytest.mark.django_db
class TestLibraryAssetFileView:
    def test_a_changed_or_remote_source_is_a_404_rather_than_other_bytes(self, client):
        import scripts.media as media
        with patch("apps.library.views.library_service.resolve_file",
                   side_effect=media.MediaError("changed: /home/owner/rain.png")):
            response = client.get(f"/api/v1/library/{ASSET_ID}/file/")
        assert response.status_code == 404

    def test_serves_the_bytes_with_range_support(self, client, tmp_path):
        source = tmp_path / "rain.png"
        source.write_bytes(b"0123456789")
        with patch("apps.library.views.library_service.resolve_file", return_value=source):
            whole = client.get(f"/api/v1/library/{ASSET_ID}/file/")
            part = client.get(f"/api/v1/library/{ASSET_ID}/file/", HTTP_RANGE="bytes=2-4")
        assert whole.status_code == 200
        assert b"".join(whole.streaming_content) == b"0123456789"
        assert part.status_code == 206
        assert b"".join(part.streaming_content) == b"234"

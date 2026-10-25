"""DRF contract tests for the per-scene visual asset endpoints.

The service layer is mocked: what a candidate is, how it is ranked and what
choosing it requires are proven in the main repo's tests/test_visual_assets.py.
These tests defend only the HTTP contract - shapes, refusals as 400s, the
approval flag never defaulting on, and the actor coming from the session.
"""
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

import scripts.visual_assets as visual_assets

BASE = "/api/v1/projects/proof/scenes/s02/assets/"


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(
        username="owner", email="owner@example.com", password="pw")


@pytest.fixture
def client(owner):
    api = APIClient()
    api.force_authenticate(user=owner)
    return api


@pytest.fixture(autouse=True)
def project_exists():
    with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "proof"}):
        yield


SERVICE = "apps.projects.views.visual_assets_service"


@pytest.mark.django_db
class TestVisualAssetViews:
    def test_status_reports_providers_gpu_and_autonomy(self, client):
        payload = {"providers": [{"provider": "pexels", "state": "ready"}],
                   "gpu": {"state": "deferred"}, "autonomy": {"level": "supervised"}}
        with patch(f"{SERVICE}.status", return_value=payload):
            response = client.get("/api/v1/projects/proof/visual-assets/")
        assert response.status_code == 200
        assert response.json()["autonomy"]["level"] == "supervised"

    def test_an_unsearched_scene_says_so_rather_than_404(self, client):
        with patch(f"{SERVICE}.scene_view", return_value=None):
            body = client.get(BASE).json()
        assert body == {"scene_id": "s02", "candidates": [], "searched": False}

    def test_search_passes_only_what_was_asked_for(self, client):
        with patch(f"{SERVICE}.search", return_value={"candidates": []}) as search:
            response = client.post(BASE, {"query": "calm lake", "kind": "video",
                                          "sources": ["library", "stock"], "more": True}, format="json")
        assert response.status_code == 200
        search.assert_called_once_with("proof", "s02", query="calm lake", kind="video",
                                       sources=["library", "stock"], more=True, pages=None)

    def test_search_rejects_an_unknown_source_or_kind(self, client):
        assert client.post(BASE, {"sources": ["torrent"]}, format="json").status_code == 400
        assert client.post(BASE, {"kind": "audio"}, format="json").status_code == 400

    def test_choosing_never_approves_unless_asked_and_records_the_session_user(self, client):
        with patch(f"{SERVICE}.select", return_value={"status": "approval_required"}) as select:
            response = client.post(BASE + "select/", {"candidate_id": "stock:pexels:image:1"}, format="json")
        assert response.status_code == 200 and response.json()["status"] == "approval_required"
        select.assert_called_once_with("proof", "s02", "stock:pexels:image:1",
                                       "owner@example.com", approve=False, note="")

    def test_approval_and_note_are_passed_through(self, client):
        with patch(f"{SERVICE}.select", return_value={"status": "selected"}) as select:
            client.post(BASE + "select/", {"candidate_id": "x", "approve": True,
                                           "note": "checked the page"}, format="json")
        assert select.call_args.kwargs == {"approve": True, "note": "checked the page"}

    def test_a_refusal_is_a_400_with_the_reason(self, client):
        error = visual_assets.VisualAssetError("the rights record is incomplete; say what you checked")
        with patch(f"{SERVICE}.select", side_effect=error):
            response = client.post(BASE + "select/", {"candidate_id": "x", "approve": True}, format="json")
        assert response.status_code == 400
        assert "incomplete" in response.json()["detail"]

    def test_a_busy_project_is_a_409_not_a_refusal(self, client):
        import scripts.project as project
        with patch(f"{SERVICE}.select", side_effect=project.ProjectBusyError("proof")):
            response = client.post(BASE + "select/", {"candidate_id": "x"}, format="json")
        assert response.status_code == 409

    def test_a_missing_candidate_id_is_a_400(self, client):
        assert client.post(BASE + "select/", {}, format="json").status_code == 400

    def test_saving_to_the_library_is_201(self, client):
        with patch(f"{SERVICE}.save", return_value={"asset_id": "a" * 64, "approved": False}):
            response = client.post(BASE + "save/", {"candidate_id": "x"}, format="json")
        assert response.status_code == 201

    def test_regenerating_a_scene(self, client):
        with patch(f"{SERVICE}.regenerate", return_value={"scene_id": "s02", "seed": 9}) as regenerate:
            response = client.post("/api/v1/projects/proof/scenes/s02/regenerate/",
                                   {"prompt": "a calmer lake"}, format="json")
        assert response.status_code == 200
        regenerate.assert_called_once_with("proof", "s02", "owner@example.com", seed=None,
                                           prompt="a calmer lake", negative_prompt=None)

    def test_an_unknown_project_is_a_404(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value=None):
            assert client.get(BASE).status_code == 404

    def test_unauthenticated_requests_are_rejected(self):
        assert APIClient().get(BASE).status_code in (401, 403)


class TestPreviewAddresses:
    def test_library_previews_are_served_by_our_own_verified_endpoint(self):
        from apps.engine.services import visual_assets as service
        entry = {"candidates": [
            {"preview": {"kind": "library", "asset_id": "a" * 64}},
            {"preview": {"url": "https://images.pexels.com/p.jpg", "kind": "image"}},
            {"preview": None}]}
        urls = [c["preview_url"] for c in service._present(entry)["candidates"]]
        assert urls == [f"/api/v1/library/{'a' * 64}/file/", "https://images.pexels.com/p.jpg", None]

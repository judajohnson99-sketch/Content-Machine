"""DRF endpoint tests: request/response contract only. The service layer is
mocked, so these tests need no filesystem, no fixtures, and no ffmpeg -
domain correctness is proven once in the main repo's tests/ (architecture
plan §17).
"""
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(username="owner", password="pw")


@pytest.fixture
def client(owner):
    api = APIClient()
    api.force_authenticate(user=owner)
    return api


@pytest.mark.django_db
class TestProjectListView:
    def test_returns_the_service_layers_summaries(self, client):
        fake = [{
            "video_id": "abc", "selected_title": "Title", "concept_id": None,
            "niche": None, "overall_status": "NEEDS_ATTENTION", "created_utc": None,
            "archived": False, "preview_image": None,
        }]
        with patch("apps.projects.views.projects_service.list_projects", return_value=fake):
            response = client.get("/api/v1/projects/")
        assert response.status_code == 200
        assert response.json() == fake

    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().get("/api/v1/projects/")
        assert response.status_code == 403

    def test_archived_projects_are_hidden_unless_asked_for(self, client):
        with patch("apps.engine.services.projects.project.list_projects", return_value=[
            {"video_id": "live", "selected_title": None, "concept_id": None, "niche": None,
             "overall_status": "DRAFT", "created_utc": None, "archived": False},
            {"video_id": "old", "selected_title": None, "concept_id": None, "niche": None,
             "overall_status": "DRAFT", "created_utc": None, "archived": True},
        ]):
            default = client.get("/api/v1/projects/").json()
            everything = client.get("/api/v1/projects/?include_archived=1").json()
        assert [p["video_id"] for p in default] == ["live"]
        assert [p["video_id"] for p in everything] == ["live", "old"]
        assert everything[1]["archived"] is True


@pytest.mark.django_db
class TestProjectArchiveView:
    def test_archives_with_the_requesting_user_as_actor(self, client, owner):
        fake = {"video_id": "abc", "selected_title": None, "concept_id": None, "niche": None,
                "overall_status": "DRAFT", "created_utc": None, "archived": True}
        with patch("apps.projects.views.projects_service.set_archived", return_value=fake) as call:
            response = client.post("/api/v1/projects/abc/archive/",
                                   {"archived": True, "reason": "demo"}, format="json")
        assert response.status_code == 200
        assert response.json()["archived"] is True
        call.assert_called_once_with("abc", True, owner.email or owner.get_username(), reason="demo")

    def test_missing_flag_is_a_400_and_unknown_project_a_404(self, client):
        import scripts.project as project
        assert client.post("/api/v1/projects/abc/archive/", {}, format="json").status_code == 400
        with patch("apps.projects.views.projects_service.set_archived",
                   side_effect=project.ProjectError("no such project: abc")):
            response = client.post("/api/v1/projects/abc/archive/", {"archived": False},
                                   format="json")
        assert response.status_code == 404

    def test_unauthenticated_post_is_rejected(self):
        response = APIClient().post("/api/v1/projects/abc/archive/", {"archived": True},
                                    format="json")
        assert response.status_code == 403


@pytest.mark.django_db
class TestProjectStatusView:
    def test_returns_404_for_an_unknown_project(self, client):
        with patch("apps.projects.views.projects_service.get_status", return_value=None):
            response = client.get("/api/v1/projects/no-such-project/status/")
        assert response.status_code == 404

    def test_returns_the_status_report_unmodified(self, client):
        fake = {
            "video_id": "abc", "recorded": "READY_FOR_REVIEW",
            "verdict": "READY_FOR_REVIEW", "blocking": [], "stale": False,
            "digest_state": "MATCHES",
        }
        with patch("apps.projects.views.projects_service.get_status", return_value=fake):
            response = client.get("/api/v1/projects/abc/status/")
        assert response.status_code == 200
        assert response.json() == fake


@pytest.mark.django_db
class TestProjectAssetsView:
    def test_returns_404_for_an_unknown_project(self, client):
        with patch("apps.projects.views.assets_service.get_assets", return_value=None):
            response = client.get("/api/v1/projects/no-such-project/assets/")
        assert response.status_code == 404

    def test_returns_the_manifest_unmodified(self, client):
        fake = {"video_id": "abc", "video": None, "thumbnails": [], "images": [],
                "audio": None, "qc": None, "storyboard": None, "package": None, "logs": []}
        with patch("apps.projects.views.assets_service.get_assets", return_value=fake):
            response = client.get("/api/v1/projects/abc/assets/")
        assert response.status_code == 200
        assert response.json() == fake


@pytest.mark.django_db
class TestProjectFileView:
    """The servable-path rule itself is scripts.project.project_file_path's
    and is proven in tests/test_project.py; here only the HTTP contract on
    top of it is exercised, against a real temp file."""

    @pytest.fixture
    def asset(self, tmp_path):
        path = tmp_path / "candidate_1.jpg"
        path.write_bytes(b"0123456789" * 10)   # 100 bytes
        return path

    def test_every_refusal_is_a_404(self, client):
        import scripts.project as project
        with patch("apps.projects.views.assets_service.resolve_file",
                   side_effect=project.ProjectError(["resolves outside the project"])):
            response = client.get("/api/v1/projects/abc/files/output/../metadata.json")
        assert response.status_code == 404

    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg")
        assert response.status_code == 403

    def test_serves_the_whole_file_with_a_content_type(self, client, asset):
        with patch("apps.projects.views.assets_service.resolve_file", return_value=asset) as res:
            response = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg")
        res.assert_called_once_with("abc", "thumbnail/candidate_1.jpg")
        assert response.status_code == 200
        assert response["Content-Type"] == "image/jpeg"
        assert response["Accept-Ranges"] == "bytes"
        assert b"".join(response.streaming_content) == b"0123456789" * 10

    def test_honours_a_byte_range(self, client, asset):
        with patch("apps.projects.views.assets_service.resolve_file", return_value=asset):
            response = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg",
                                  HTTP_RANGE="bytes=10-19")
        assert response.status_code == 206
        assert response["Content-Range"] == "bytes 10-19/100"
        assert response["Content-Length"] == "10"
        assert b"".join(response.streaming_content) == b"0123456789"

    def test_open_ended_and_suffix_ranges(self, client, asset):
        with patch("apps.projects.views.assets_service.resolve_file", return_value=asset):
            tail = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg",
                              HTTP_RANGE="bytes=95-")
            suffix = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg",
                                HTTP_RANGE="bytes=-5")
        assert tail["Content-Range"] == "bytes 95-99/100"
        assert suffix["Content-Range"] == "bytes 95-99/100"
        assert b"".join(tail.streaming_content) == b"56789"

    def test_unsatisfiable_range_is_416(self, client, asset):
        with patch("apps.projects.views.assets_service.resolve_file", return_value=asset):
            response = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg",
                                  HTTP_RANGE="bytes=500-600")
        assert response.status_code == 416
        assert response["Content-Range"] == "bytes */100"


@pytest.mark.django_db
class TestProjectCreateView:
    def _summary(self):
        return {"video_id": "calm-001", "selected_title": None, "concept_id": "sleep-brown-noise-dark",
                "niche": "adult_sleep", "overall_status": "DRAFT", "created_utc": "2026-09-17T00:00:00Z"}

    def test_scaffolds_and_returns_the_new_summary(self, client):
        with patch("apps.projects.views.concepts_service.create_project",
                   return_value=self._summary()) as create:
            response = client.post("/api/v1/projects/",
                                   {"video_id": "calm-001", "concept_id": "sleep-brown-noise-dark",
                                    "duration": 600}, format="json")
        assert response.status_code == 201
        assert response.json()["video_id"] == "calm-001"
        create.assert_called_once_with("calm-001", "sleep-brown-noise-dark", duration=600.0)

    def test_duration_is_optional(self, client):
        with patch("apps.projects.views.concepts_service.create_project",
                   return_value=self._summary()) as create:
            client.post("/api/v1/projects/",
                        {"video_id": "calm-001", "concept_id": "sleep-brown-noise-dark"}, format="json")
        create.assert_called_once_with("calm-001", "sleep-brown-noise-dark", duration=None)

    def test_malformed_id_is_refused_before_the_domain_layer(self, client):
        with patch("apps.projects.views.concepts_service.create_project") as create:
            response = client.post("/api/v1/projects/",
                                   {"video_id": "../etc", "concept_id": "x"}, format="json")
        assert response.status_code == 400
        create.assert_not_called()

    def test_existing_project_is_a_409(self, client):
        import scripts.experiment as experiment
        with patch("apps.projects.views.concepts_service.create_project",
                   side_effect=experiment.ScaffoldError("exists", "project already exists: calm-001")):
            response = client.post("/api/v1/projects/",
                                   {"video_id": "calm-001", "concept_id": "sleep-brown-noise-dark"},
                                   format="json")
        assert response.status_code == 409
        assert response.json()["code"] == "exists"

    def test_unknown_concept_is_a_400(self, client):
        import scripts.experiment as experiment
        with patch("apps.projects.views.concepts_service.create_project",
                   side_effect=experiment.ScaffoldError("unknown_concept", "no such concept: nope")):
            response = client.post("/api/v1/projects/",
                                   {"video_id": "calm-001", "concept_id": "nope"}, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "unknown_concept"

    def test_unauthenticated_create_is_rejected(self):
        response = APIClient().post("/api/v1/projects/",
                                    {"video_id": "calm-001", "concept_id": "x"}, format="json")
        assert response.status_code == 403


@pytest.mark.django_db
class TestProjectGpuJobsView:
    def test_returns_404_for_an_unknown_project(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value=None):
            response = client.get("/api/v1/projects/nope/gpu-jobs/")
        assert response.status_code == 404

    def test_returns_the_queues_own_view(self, client):
        fake = [{"job_id": "abc", "state": "QUEUED", "wait_reason": "WAITING_FOR_CAPABLE_WORKER"}]
        with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "vid"}), \
             patch("apps.projects.views.workers_service.project_jobs", return_value=fake):
            response = client.get("/api/v1/projects/vid/gpu-jobs/")
        assert response.status_code == 200
        assert response.json() == fake


@pytest.mark.django_db
class TestProjectResearchBriefView:
    def _brief(self):
        return {"video_id": "vid", "niche": "sleep ambience", "creative_intent": "cozy",
                "likes": [], "dislikes": [], "seed_references": [], "notes": None,
                "updated_utc": "2026-09-19T00:00:00Z"}

    def test_get_returns_404_for_an_unknown_project(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value=None):
            response = client.get("/api/v1/projects/nope/research/brief/")
        assert response.status_code == 404

    def test_get_returns_404_when_no_brief_exists_yet(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.get_brief", return_value=None):
            response = client.get("/api/v1/projects/vid/research/brief/")
        assert response.status_code == 404

    def test_get_returns_the_stored_brief(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.get_brief", return_value=self._brief()):
            response = client.get("/api/v1/projects/vid/research/brief/")
        assert response.status_code == 200
        assert response.json() == self._brief()

    def test_put_saves_a_valid_brief(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.save_brief",
                   return_value=self._brief()) as save:
            response = client.put("/api/v1/projects/vid/research/brief/",
                                  {"niche": "sleep ambience", "creative_intent": "cozy"},
                                  format="json")
        assert response.status_code == 200
        save.assert_called_once()
        assert save.call_args[0][0] == "vid"
        assert save.call_args[0][1]["niche"] == "sleep ambience"

    def test_put_missing_niche_is_a_400_before_the_domain_layer(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.save_brief") as save:
            response = client.put("/api/v1/projects/vid/research/brief/", {}, format="json")
        assert response.status_code == 400
        save.assert_not_called()

    def test_put_bad_seed_reference_type_is_a_400(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.save_brief") as save:
            response = client.put(
                "/api/v1/projects/vid/research/brief/",
                {"niche": "n", "seed_references": [{"type": "podcast", "value": "x"}]},
                format="json")
        assert response.status_code == 400
        save.assert_not_called()

    def test_domain_layer_refusal_is_also_a_400(self, client):
        import scripts.research as research
        with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.save_brief",
                   side_effect=research.ResearchError("no good")):
            response = client.put("/api/v1/projects/vid/research/brief/",
                                  {"niche": "n"}, format="json")
        assert response.status_code == 400

    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().get("/api/v1/projects/vid/research/brief/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestProjectResearchFindingsView:
    def test_returns_404_for_an_unknown_project(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value=None):
            response = client.get("/api/v1/projects/nope/research/findings/")
        assert response.status_code == 404

    def test_returns_404_when_no_findings_exist_yet(self, client):
        with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.get_findings", return_value=None):
            response = client.get("/api/v1/projects/vid/research/findings/")
        assert response.status_code == 404

    def test_returns_the_stored_findings(self, client):
        fake = {"video_id": "vid", "findings": [
            {"finding_id": "f1", "kind": "observation", "topic": "pacing",
             "statement": "x", "source_url": "https://x.test", "source_title": "x",
             "confidence": "VERIFIED"},
        ]}
        with patch("apps.projects.views.projects_service.get_metadata", return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.get_findings", return_value=fake):
            response = client.get("/api/v1/projects/vid/research/findings/")
        assert response.status_code == 200
        assert response.json() == fake


SUMMARY = {
    "video_id": "vid", "selected_title": "T", "concept_id": "vid", "niche": "n",
    "overall_status": "DRAFT", "created_utc": None, "archived": False,
    "preview_image": None,
}


@pytest.mark.django_db
class TestProductionFromGoalView:
    """A production from a sentence. The derivation itself (and everything it
    refuses to claim) is proven in tests/test_longform_production.py; this is
    the request/response contract only."""

    def test_a_goal_creates_a_production(self, client):
        derived = {"project": SUMMARY, "plan": {"title_pattern": "T"},
                   "brief": {"niche": "n"}, "concept_id": "vid"}
        with patch("apps.projects.views.concepts_service.create_from_goal",
                   return_value=derived) as create:
            response = client.post("/api/v1/projects/from-goal/", {
                "goal": "a 30-minute rainy window study session",
                "excerpt_seconds": 90,
            }, format="json")
        assert response.status_code == 201
        assert response.json()["project"]["video_id"] == "vid"
        assert create.call_args.kwargs["excerpt_seconds"] == 90

    def test_a_goal_that_is_not_a_sentence_is_refused(self, client):
        response = client.post("/api/v1/projects/from-goal/", {"goal": "sleep"},
                               format="json")
        assert response.status_code == 400

    def test_a_derivation_failure_is_a_400_not_a_500(self, client):
        with patch("apps.projects.views.concepts_service.create_from_goal",
                   side_effect=RuntimeError("the model said no")):
            response = client.post("/api/v1/projects/from-goal/",
                                   {"goal": "a long ambient sleep video"},
                                   format="json")
        assert response.status_code == 400
        assert "derivation_failed" in str(response.json())

    def test_unauthenticated_requests_are_rejected(self):
        response = APIClient().post("/api/v1/projects/from-goal/",
                                    {"goal": "a long ambient sleep video"},
                                    format="json")
        assert response.status_code == 403


@pytest.mark.django_db
class TestProjectDeleteView:

    def test_deletion_requires_the_id_to_be_echoed(self, client):
        with patch("apps.projects.views.projects_service.delete") as delete:
            response = client.delete("/api/v1/projects/vid/",
                                     {"confirm_video_id": "other"}, format="json")
        assert response.status_code == 400
        delete.assert_not_called()

    def test_a_confirmed_deletion_names_the_actor(self, client, owner):
        with patch("apps.projects.views.projects_service.delete",
                   return_value={"video_id": "vid", "removed": ["projects/vid"],
                                 "actor": "owner", "reason": "", "deleted_utc": "x"}) as delete:
            response = client.delete("/api/v1/projects/vid/",
                                     {"confirm_video_id": "vid", "reason": "residue"},
                                     format="json")
        assert response.status_code == 200
        assert delete.call_args.args[1] == owner.get_username()
        assert delete.call_args.kwargs["reason"] == "residue"


@pytest.mark.django_db
class TestResearchInfluenceView:

    def test_404_until_research_has_run(self, client):
        with patch("apps.projects.views.projects_service.get_metadata",
                   return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.get_influence", return_value=None):
            response = client.get("/api/v1/projects/vid/research/influence/")
        assert response.status_code == 404

    def test_returns_the_decisions_and_what_was_applied(self, client):
        fake = {"video_id": "vid", "researched": True, "decisions": [
            {"parameter": "seconds_per_scene", "value": 24.0, "rationale": "r",
             "evidence_finding_ids": ["f1"], "source_urls": ["https://x.test"],
             "confidence": "VERIFIED"}],
            "applied": {"seconds_per_scene": 24.0}, "suggested_not_applied": []}
        with patch("apps.projects.views.projects_service.get_metadata",
                   return_value={"video_id": "vid"}), \
             patch("apps.projects.views.research_service.get_influence", return_value=fake):
            response = client.get("/api/v1/projects/vid/research/influence/")
        assert response.status_code == 200
        assert response.json() == fake


@pytest.mark.django_db
class TestProjectOwnerMediaView:
    def selection(self, **overrides):
        base = {
            "updated_utc": "2026-10-07T12:00:00Z", "actor": "owner",
            "roles": {role: {"label": role.title(), "count": 0, "entries": []}
                      for role in ("visuals", "music", "ambience", "sfx")},
            "problems": [], "stale_stages": [],
        }
        base.update(overrides)
        return base

    def test_returns_the_selection_and_404s_for_an_unknown_project(self, client):
        with patch("apps.projects.views.library_service.selection",
                   return_value=self.selection()):
            response = client.get("/api/v1/projects/abc/owner-media/")
        assert response.status_code == 200
        assert set(response.json()["roles"]) == {"visuals", "music", "ambience", "sfx"}
        with patch("apps.projects.views.library_service.selection", return_value=None):
            assert client.get("/api/v1/projects/abc/owner-media/").status_code == 404

    def test_choosing_media_names_the_stages_it_makes_stale(self, client, owner):
        result = {"selection": self.selection(), "changed_roles": ["visuals"],
                  "stale_stages": ["scenes"]}
        with patch("apps.projects.views.projects_service.get_metadata", return_value={}), \
                patch("apps.projects.views.library_service.select",
                      return_value=result) as call:
            response = client.put("/api/v1/projects/abc/owner-media/",
                                  {"visuals": ["a" * 64, "b" * 64]}, format="json")
        assert response.status_code == 200
        assert response.json()["stale_stages"] == ["scenes"]
        call.assert_called_once_with(
            "abc", {"visuals": ["a" * 64, "b" * 64]},
            owner.email or owner.get_username())

    def test_an_empty_body_is_a_400_and_a_refused_selection_is_a_400_with_the_reason(self, client):
        import scripts.project as project
        with patch("apps.projects.views.projects_service.get_metadata", return_value={}):
            assert client.put("/api/v1/projects/abc/owner-media/", {},
                              format="json").status_code == 400
            with patch("apps.projects.views.library_service.select",
                       side_effect=project.ProjectError(
                           "Asset abc needs explicit rights and evidence")):
                response = client.put("/api/v1/projects/abc/owner-media/",
                                      {"music": ["c" * 64]}, format="json")
        assert response.status_code == 400
        assert "rights" in response.json()["detail"]

    def test_clearing_a_role_is_an_empty_list_not_a_missing_one(self, client):
        result = {"selection": self.selection(), "changed_roles": ["music"],
                  "stale_stages": ["audio"]}
        with patch("apps.projects.views.projects_service.get_metadata", return_value={}), \
                patch("apps.projects.views.library_service.select",
                      return_value=result) as call:
            response = client.put("/api/v1/projects/abc/owner-media/",
                                  {"music": []}, format="json")
        assert response.status_code == 200
        assert call.call_args[0][1] == {"music": []}

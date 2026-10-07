"""Adapter tests: does apps.engine.services call the right domain function
with the right arguments and return its result unmodified? Domain
correctness itself is proven once, by tests/ in the main repo (see the
architecture plan §17) - these tests only prove the plumbing.
"""
from unittest.mock import patch

from apps.engine.services import projects as projects_service


class TestListProjects:
    def test_delegates_to_the_domain_function_and_hides_archived_projects(self):
        # Archiving is presentation, so the filter lives in the adapter: the
        # domain still lists everything.
        fake = [{"video_id": "abc"}, {"video_id": "old", "archived": True}]
        with patch("scripts.project.list_projects", return_value=fake) as mocked:
            result = projects_service.list_projects()
        mocked.assert_called_once_with()
        assert result == [{"video_id": "abc"}]

    def test_include_archived_returns_the_domain_list_unmodified(self):
        fake = [{"video_id": "abc"}, {"video_id": "old", "archived": True}]
        with patch("scripts.project.list_projects", return_value=fake):
            assert projects_service.list_projects(include_archived=True) is fake


class TestGetStatus:
    def test_delegates_to_status_report_with_the_given_video_id(self):
        fake = {"video_id": "abc", "verdict": "READY_FOR_REVIEW"}
        with patch("scripts.project.status_report", return_value=fake) as mocked:
            result = projects_service.get_status("abc")
        mocked.assert_called_once_with("abc")
        assert result is fake

    def test_returns_none_for_an_unknown_project(self):
        with patch("scripts.project.status_report", return_value=None):
            assert projects_service.get_status("no-such-project") is None

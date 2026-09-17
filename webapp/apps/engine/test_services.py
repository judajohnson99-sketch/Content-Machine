"""Adapter tests: does apps.engine.services call the right domain function
with the right arguments and return its result unmodified? Domain
correctness itself is proven once, by tests/ in the main repo (see the
architecture plan §17) - these tests only prove the plumbing.
"""
from unittest.mock import patch

from apps.engine.services import projects as projects_service


class TestListProjects:
    def test_delegates_to_the_domain_function_unmodified(self):
        fake = [{"video_id": "abc"}]
        with patch("scripts.project.list_projects", return_value=fake) as mocked:
            result = projects_service.list_projects()
        mocked.assert_called_once_with()
        assert result is fake


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

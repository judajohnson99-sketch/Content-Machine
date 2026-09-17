"""DRF endpoint tests: request/response contract only; the catalogue's
correctness is proven in the main repo's tests/test_experiment.py."""
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
class TestConceptListView:
    def test_returns_the_catalogue_unmodified(self, client):
        fake = {"capabilities": {"search_available": False}, "concepts": [{"id": "x"}]}
        with patch("apps.concepts.views.concepts_service.concept_catalog", return_value=fake):
            response = client.get("/api/v1/concepts/")
        assert response.status_code == 200
        assert response.json() == fake

    def test_unauthenticated_request_is_rejected(self):
        assert APIClient().get("/api/v1/concepts/").status_code == 403

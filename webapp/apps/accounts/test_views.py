"""Contract tests for the app's own sign-in surface.

The point of these: an unauthenticated caller can reach exactly one endpoint
(the login POST) and nothing else, a bad password is refused without saying
which half was wrong, and the identity the app reports is the same string a
review decision will be attributed to.
"""
import pytest
from rest_framework.test import APIClient


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(
        username="owner", password="pytest-only-pw", email="owner@example.com")


@pytest.mark.django_db
class TestSessionView:
    def test_login_sets_a_session_and_returns_the_reviewer_identity(self, owner):
        client = APIClient()
        response = client.post("/api/v1/auth/session/",
                               {"username": "owner", "password": "pytest-only-pw"},
                               format="json")
        assert response.status_code == 200
        assert response.data["username"] == "owner"
        assert response.data["reviewer"] == "owner@example.com"
        assert response.data["csrf_token"]
        # The session is live: a protected endpoint now answers.
        assert client.get("/api/v1/auth/me/").status_code == 200

    def test_wrong_password_is_401_and_does_not_say_which_half_was_wrong(self, owner):
        client = APIClient()
        response = client.post("/api/v1/auth/session/",
                               {"username": "owner", "password": "wrong"},
                               format="json")
        assert response.status_code == 401
        assert response.data["detail"] == "Incorrect username or password."

    def test_unknown_user_gives_the_same_answer_as_a_wrong_password(self, db):
        client = APIClient()
        response = client.post("/api/v1/auth/session/",
                               {"username": "nobody", "password": "wrong"},
                               format="json")
        assert response.status_code == 401
        assert response.data["detail"] == "Incorrect username or password."

    def test_logout_ends_the_session(self, owner):
        client = APIClient()
        client.post("/api/v1/auth/session/",
                    {"username": "owner", "password": "pytest-only-pw"}, format="json")
        assert client.delete("/api/v1/auth/session/").status_code == 204
        assert client.get("/api/v1/auth/me/").status_code == 403


@pytest.mark.django_db
class TestMeView:
    def test_unauthenticated_is_rejected(self):
        assert APIClient().get("/api/v1/auth/me/").status_code == 403

    def test_reviewer_falls_back_to_the_username_without_an_email(self, django_user_model):
        django_user_model.objects.create_user(username="solo", password="pytest-only-pw")
        client = APIClient()
        client.post("/api/v1/auth/session/",
                    {"username": "solo", "password": "pytest-only-pw"}, format="json")
        response = client.get("/api/v1/auth/me/")
        assert response.data["reviewer"] == "solo"
        assert response.data["email"] == ""

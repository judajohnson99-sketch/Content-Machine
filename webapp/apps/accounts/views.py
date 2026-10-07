"""The app's own front door.

Deliberately thin: `django.contrib.auth.authenticate`/`login`/`logout` do the
work, so password hashing, session rotation and the session cookie's flags
stay exactly what Django's admin login already used. Nothing here grants any
authority the admin login did not - it is the same account and the same
session - and reviewer identity is still read from `request.user` at the
point of decision, never from a client.
"""
from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import IdentitySerializer, LoginSerializer


def identity(user):
    """The identity payload, including the string reviews are attributed to.

    Mirrors apps/review/views.py's `request.user.email or get_username()` so
    the UI can show a reviewer exactly who a decision will be recorded as,
    before they make it.
    """
    email = user.email or ''
    return {
        'username': user.get_username(),
        'email': email,
        'reviewer': email or user.get_username(),
    }


class SessionView(APIView):
    """POST to sign in, DELETE to sign out."""

    # The login endpoint is the one thing an unauthenticated caller must be
    # able to reach; everything else in the API stays IsAuthenticated.
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request,
                            username=serializer.validated_data['username'],
                            password=serializer.validated_data['password'])
        if user is None:
            # One message for "no such user" and "wrong password" alike: a
            # sign-in form should not enumerate accounts.
            return Response({'detail': 'Incorrect username or password.'},
                            status=status.HTTP_401_UNAUTHORIZED)
        login(request, user)
        payload = IdentitySerializer(identity(user)).data
        # A fresh CSRF token for the new session, so the SPA's next mutation
        # does not fail on the pre-login token.
        payload['csrf_token'] = get_token(request)
        return Response(payload)

    def delete(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """Who am I? The SPA calls this on boot to decide login vs app."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(IdentitySerializer(identity(request.user)).data)

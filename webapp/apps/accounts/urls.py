"""Session login/logout/identity for the single-owner control center.

Until now the SPA had no login surface at all: an unauthenticated user got a
403 and error copy telling them to go to Django's /admin/login/ and come
back. These three endpoints let the app own its own front door. They add no
new authentication mechanism - it is still Django's session cookie and the
same single-owner account - they just make it reachable from the app.
"""
from django.urls import path

from . import views

urlpatterns = [
    path('session/', views.SessionView.as_view(), name='auth-session'),
    path('me/', views.MeView.as_view(), name='auth-me'),
]

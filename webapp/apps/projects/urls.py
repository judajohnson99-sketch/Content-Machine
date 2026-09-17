from django.urls import path

from . import views

urlpatterns = [
    path("", views.ProjectListView.as_view(), name="project-list"),
    path("<str:video_id>/", views.ProjectDetailView.as_view(), name="project-detail"),
    path("<str:video_id>/status/", views.ProjectStatusView.as_view(), name="project-status"),
]

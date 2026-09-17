from django.urls import path

from . import views

urlpatterns = [
    path("", views.RecentPipelineRunsView.as_view(), name="pipeline-run-recent"),
]

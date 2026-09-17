from django.urls import path

from . import views

urlpatterns = [
    path("<str:video_id>/research/", views.ResearchActionView.as_view(), name="stage-research"),
    path("<str:video_id>/creative/", views.CreativeActionView.as_view(), name="stage-creative"),
    path("<str:video_id>/storyboard/", views.StoryboardActionView.as_view(), name="stage-storyboard"),
    path("<str:video_id>/scenes/", views.ScenesActionView.as_view(), name="stage-scenes"),
    path("<str:video_id>/audio/", views.AudioActionView.as_view(), name="stage-audio"),
    path("<str:video_id>/visuals/", views.VisualsActionView.as_view(), name="stage-visuals"),
    path("<str:video_id>/run/", views.RunActionView.as_view(), name="stage-run"),
    path("<str:video_id>/produce/", views.ProduceActionView.as_view(), name="stage-produce"),
    path("<str:video_id>/pipeline-runs/",
         views.PipelineRunListView.as_view(), name="pipeline-run-list"),
    path("<str:video_id>/pipeline-runs/<int:run_id>/",
         views.PipelineRunDetailView.as_view(), name="pipeline-run-detail"),
]

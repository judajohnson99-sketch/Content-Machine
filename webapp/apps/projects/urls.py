from django.urls import path

from . import views

urlpatterns = [
    path("", views.ProjectListView.as_view(), name="project-list"),
    path("from-goal/", views.ProductionFromGoalView.as_view(), name="project-from-goal"),
    path("<str:video_id>/", views.ProjectDetailView.as_view(), name="project-detail"),
    path("<str:video_id>/status/", views.ProjectStatusView.as_view(), name="project-status"),
    path("<str:video_id>/archive/", views.ProjectArchiveView.as_view(), name="project-archive"),
    path("<str:video_id>/assets/", views.ProjectAssetsView.as_view(), name="project-assets"),
    path("<str:video_id>/files/<path:relative>", views.ProjectFileView.as_view(),
         name="project-file"),
    path("<str:video_id>/gpu-jobs/", views.ProjectGpuJobsView.as_view(),
         name="project-gpu-jobs"),
    path("<str:video_id>/research/brief/", views.ProjectResearchBriefView.as_view(),
         name="project-research-brief"),
    path("<str:video_id>/research/findings/", views.ProjectResearchFindingsView.as_view(),
         name="project-research-findings"),
    path("<str:video_id>/research/influence/", views.ProjectResearchInfluenceView.as_view(),
         name="project-research-influence"),
]

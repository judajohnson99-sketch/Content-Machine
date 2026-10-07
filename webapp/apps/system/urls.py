from django.urls import path

from . import views

urlpatterns = [
    path("readiness/", views.ReadinessView.as_view(), name="system-readiness"),
    path("gpu-jobs/", views.GpuJobListView.as_view(), name="system-gpu-jobs"),
    path("gpu-jobs/<str:job_id>/assets/<int:index>/",
         views.GpuJobAssetView.as_view(), name="system-gpu-job-asset"),
    path("gpu-jobs/<str:job_id>/assets/<int:index>/save/",
         views.GpuJobAssetSaveView.as_view(), name="system-gpu-job-asset-save"),
    path("gpu-jobs/<str:job_id>/requeue/",
         views.GpuJobRequeueView.as_view(), name="system-gpu-job-requeue"),
]

from django.urls import path

from . import views

urlpatterns = [
    path("readiness/", views.ReadinessView.as_view(), name="system-readiness"),
    path("gpu-jobs/", views.GpuJobListView.as_view(), name="system-gpu-jobs"),
]

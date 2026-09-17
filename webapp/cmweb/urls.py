"""URL configuration for cmweb.

/api/v1/ is versioned per the architecture plan; each resource's URLs live
in its own app (apps/projects/urls.py etc.) and are included here.
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/projects/', include('apps.projects.urls')),
    path('api/v1/projects/', include('apps.pipeline.urls')),
    path('api/v1/projects/', include('apps.review.urls')),
]

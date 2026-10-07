"""URL configuration for cmweb.

/api/v1/ is versioned per the architecture plan; each resource's URLs live
in its own app (apps/projects/urls.py etc.) and are included here.
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/auth/', include('apps.accounts.urls')),
    path('api/v1/projects/', include('apps.projects.urls')),
    path('api/v1/projects/', include('apps.pipeline.urls')),
    path('api/v1/projects/', include('apps.review.urls')),
    path('api/v1/concepts/', include('apps.concepts.urls')),
    path('api/v1/system/', include('apps.system.urls')),
    path('api/v1/pipeline-runs/', include('apps.pipeline.urls_global')),
]

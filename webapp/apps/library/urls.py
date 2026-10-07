from django.urls import path

from . import views

urlpatterns = [
    path("", views.LibraryListView.as_view(), name="library-list"),
    path("scan/", views.LibraryScanView.as_view(), name="library-scan"),
    path("connections/", views.LibraryConnectionsView.as_view(), name="library-connections"),
    path("discover/", views.LibraryDiscoveryView.as_view(), name="library-discovery"),
    path("suggestions/", views.LibrarySuggestionsView.as_view(), name="library-suggestions"),
    path("<str:identity>/retrieve/", views.LibraryRetrieveView.as_view(), name="library-retrieve"),
    path("<str:identity>/analyze/", views.LibraryAnalyzeView.as_view(), name="library-analyze"),
    path("<str:identity>/", views.LibraryAssetView.as_view(), name="library-asset"),
    path("<str:identity>/file/", views.LibraryAssetFileView.as_view(),
         name="library-asset-file"),
]

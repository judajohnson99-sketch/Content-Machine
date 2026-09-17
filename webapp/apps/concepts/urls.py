from django.urls import path

from . import views

urlpatterns = [
    path("", views.ConceptListView.as_view(), name="concept-list"),
]

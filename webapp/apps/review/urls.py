from django.urls import path

from . import views

urlpatterns = [
    path("<str:video_id>/review-decisions/",
         views.ReviewDecisionListCreateView.as_view(), name="review-decision-list"),
]

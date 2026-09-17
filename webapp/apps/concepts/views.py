"""DRF view for /api/v1/concepts/.

Translation only (architecture plan §8): the catalogue, including each
concept's host readiness, is scripts.experiment.concept_catalog()'s read
model, returned verbatim.
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.engine.services import concepts as concepts_service


class ConceptListView(APIView):
    """GET /api/v1/concepts/ - ranked concepts + what this host can run."""

    def get(self, request):
        return Response(concepts_service.concept_catalog())

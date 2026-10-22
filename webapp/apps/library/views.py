"""DRF views for /api/v1/library/: the owner's own media, and what a
production does with it.

Translation only (architecture plan §8). What may be selected, what a role
means, which bytes get staged and which gate a selection must still pass are
all decisions in scripts/ownermedia.py and scripts/media.py; nothing here
re-derives or relaxes them. In particular no view sets a production-grade
claim - choosing your own footage is not a judgement that it is good.
"""
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

import scripts.media as media
import scripts.ownermedia as ownermedia

from apps.engine.http import serve_file
from apps.engine.services import library as library_service

from .serializers import LibraryAnnotationSerializer, LibraryScanSerializer


class LibraryListView(APIView):
    """GET /api/v1/library/[?kind=image|video|audio][&query=...] - the catalog.

    Every asset the owner has cataloged, each with what stands between it
    and being usable: no description yet, no rights record, or bytes that
    live on a machine this host cannot read. Usable ones sort first.
    """

    def get(self, request):
        kind = request.query_params.get("kind") or None
        if kind is not None and kind not in ("image", "video", "audio"):
            raise ValidationError({"kind": "must be image, video or audio"})
        try:
            assets = library_service.browse(
                query=request.query_params.get("query", ""), kind=kind)
        except media.MediaError as e:
            raise ValidationError({"detail": str(e)}) from e
        return Response({
            "assets": assets,
            "roles": [{"role": role, "label": ownermedia.ROLE_LABELS[role],
                       "kinds": list(ownermedia.ROLE_KINDS[role])}
                      for role in ownermedia.ROLES],
        })


class LibraryConnectionsView(APIView):
    def get(self, request):
        return Response(library_service.connections())


class LibraryDiscoveryView(APIView):
    def get(self, request):
        try:
            return Response({"assets": library_service.discover(request.query_params.get("query", ""), request.query_params.get("kind", "image"))})
        except media.MediaError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

    def post(self, request):
        try:
            return Response(library_service.import_discovered(request.data.get("id"), request.data.get("kind", "image")), status=201)
        except media.MediaError as exc:
            raise ValidationError({"detail": str(exc)}) from exc


class LibraryRetrieveView(APIView):
    def post(self, request, identity):
        try:
            return Response(library_service.retrieve(identity, request.data.get("mode", "source")), status=202)
        except media.MediaError as exc:
            raise ValidationError({"detail": str(exc)}) from exc


class LibraryAnalyzeView(APIView):
    def post(self, request, identity):
        try:
            return Response(library_service.analyze(identity))
        except (media.MediaError, OSError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc


class LibrarySuggestionsView(APIView):
    def get(self, request):
        try:
            return Response(library_service.suggest(request.query_params.get("project", ""), request.query_params.get("role", "visuals")))
        except (ValueError, OSError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc


class LibraryScanView(APIView):
    """POST /api/v1/library/scan/ - catalog a folder (or file) of originals.

    Synchronous, because the person who typed the path is waiting to see
    what was found and the answer is useless later. It fully decodes every
    file it finds, so a large library takes a while; the catalog is written
    per file, so an interrupted scan keeps what it had already inspected.
    """

    def post(self, request):
        serializer = LibraryScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            report = library_service.scan(serializer.validated_data["path"])
        except (media.MediaError, OSError) as e:
            raise ValidationError({"detail": str(e)}) from e
        return Response(report)


class LibraryAssetView(APIView):
    """GET /api/v1/library/{id}/ - one asset. PUT - annotate it.

    The annotation is the owner's own account of what the media shows,
    where it came from and what rights they hold. It is the only way those
    claims are ever recorded: nothing is inferred from a filename, and
    "royalty-free" in a folder name is not a licence.
    """

    def get(self, request, identity):
        asset = library_service.describe(identity)
        if asset is None:
            raise NotFound(f"no such asset: {identity}")
        return Response(asset)

    def put(self, request, identity):
        serializer = LibraryAnnotationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            library_service.annotate(
                identity, description=data["description"], source=data["source"],
                tags=data["tags"], origin=data["origin"], rights=data["rights"])
        except ownermedia.OwnerMediaError as e:
            raise ValidationError({"detail": str(e)}) from e
        return Response(library_service.describe(identity))


class LibraryAssetFileView(APIView):
    """GET /api/v1/library/{id}/file/ - the asset's own bytes, for preview.

    Served through scripts.media.resolve(), which hands back a location
    whose bytes still hash to the asset's identity and refuses one that is
    missing, changed, or only present on another machine. Byte ranges are
    honoured so a <video> or <audio> element can seek without downloading
    the whole file.
    """

    def get(self, request, identity):
        try:
            path = library_service.resolve_file(identity)
        except media.MediaError as e:
            # Unknown, changed, missing and remote all answer the same way:
            # there is nothing here to preview.
            raise NotFound(str(e)) from e
        return serve_file(path, request)

"""DRF views for /api/v1/projects/.

Translation only - HTTP in, a call into apps.engine.services, JSON (or a
file) out. No pipeline/gate logic here (architecture plan §8, "keep the web
layer thin").
"""
import mimetypes
import re

from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

import scripts.project as project

from apps.engine.exceptions import ProjectNotFound
from apps.engine.services import assets as assets_service
from apps.engine.services import projects as projects_service

from .serializers import ProjectSummarySerializer, StatusReportSerializer


class ProjectListView(APIView):
    """GET /api/v1/projects/ - live-computed, no cache (architecture plan §9)."""

    def get(self, request):
        summaries = projects_service.list_projects()
        return Response(ProjectSummarySerializer(summaries, many=True).data)


class ProjectDetailView(APIView):
    """GET /api/v1/projects/{id}/ - raw metadata.json."""

    def get(self, request, video_id):
        metadata = projects_service.get_metadata(video_id)
        if metadata is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(metadata)


class ProjectStatusView(APIView):
    """GET /api/v1/projects/{id}/status/ - status_report(), not a re-derivation."""

    def get(self, request, video_id):
        report = projects_service.get_status(video_id)
        if report is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(StatusReportSerializer(report).data)


class ProjectAssetsView(APIView):
    """GET /api/v1/projects/{id}/assets/ - project_assets() verbatim.

    Like the detail view, the domain read model is the contract: this view
    adds no fields and derives nothing.
    """

    def get(self, request, video_id):
        manifest = assets_service.get_assets(video_id)
        if manifest is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(manifest)


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 512 * 1024


def _iter_slice(handle, remaining):
    try:
        while remaining > 0:
            chunk = handle.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


class ProjectFileView(APIView):
    """GET /api/v1/projects/{id}/files/{path} - one servable asset.

    Which paths are servable is decided entirely by
    scripts.project.project_file_path() - the same rule for every adapter.
    Supports a single byte range so the browser's <video> element can seek
    a long render without downloading all of it first; anything else about
    the request is refused with 416 rather than guessed at.
    """

    def get(self, request, video_id, relative):
        try:
            path = assets_service.resolve_file(video_id, relative)
        except project.ProjectError as e:
            # Every refusal - traversal, wrong directory, missing file - is
            # a 404, so a probe learns nothing about what exists.
            raise ProjectNotFound(str(e)) from e

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        header = request.headers.get("Range")

        if not header:
            response = FileResponse(open(path, "rb"), content_type=content_type)
            response["Content-Length"] = size
            response["Accept-Ranges"] = "bytes"
            return response

        match = _RANGE.match(header.strip())
        if not match or (not match.group(1) and not match.group(2)):
            return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}"})
        start_raw, end_raw = match.groups()
        if start_raw:
            start = int(start_raw)
            end = min(int(end_raw), size - 1) if end_raw else size - 1
        else:
            # Suffix range: the last N bytes.
            length = min(int(end_raw), size)
            start, end = size - length, size - 1
        if size == 0 or start >= size or start > end:
            return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}"})

        handle = open(path, "rb")
        handle.seek(start)
        response = StreamingHttpResponse(
            _iter_slice(handle, end - start + 1), status=206, content_type=content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = end - start + 1
        response["Accept-Ranges"] = "bytes"
        return response

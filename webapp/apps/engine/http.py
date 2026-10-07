"""Streaming a single file over HTTP, with optional byte-range support.

Shared by every view that serves a file straight off disk (project assets,
GPU-worker job assets) so the range-request handling - what a browser's
<video>/<img> element relies on to seek - is written once.
"""
import mimetypes
import re

from django.http import FileResponse, HttpResponse, StreamingHttpResponse

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


def serve_file(path, request):
    """A `FileResponse`/`StreamingHttpResponse` for `path`, honouring a
    single `Range: bytes=...` header. Anything else about the request is
    refused with 416 rather than guessed at."""
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

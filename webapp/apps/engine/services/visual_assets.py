"""Thin adapter for the unified visual asset workflow.

No business logic: scripts.visual_assets finds, ranks and chooses;
scripts.asset_licensing decides who may approve what. This module only calls
them and gives each candidate a preview address the browser can load.
"""
import scripts.visual_assets as visual_assets

VisualAssetError = visual_assets.VisualAssetError


def _present(entry):
    """The stored discovery with a ``preview_url`` on every candidate.

    A stock preview is the provider's own thumbnail address; a library one is
    served from our own file endpoint (which hash-verifies the bytes). A
    candidate with no picture yet (a planned generation) has none.
    """
    if not entry:
        return entry
    for candidate in entry.get("candidates", []):
        preview = candidate.get("preview") or {}
        if preview.get("kind") == "library":
            candidate["preview_url"] = f"/api/v1/library/{preview['asset_id']}/file/"
        else:
            candidate["preview_url"] = preview.get("url")
    return entry


def status(video_id=None):
    return visual_assets.status(video_id)


def scene_view(video_id, scene_id):
    return _present(visual_assets.scene_view(video_id, scene_id))


def search(video_id, scene_id, *, query=None, kind=None, sources=None, more=False, pages=None):
    return _present(visual_assets.discover(
        video_id, scene_id, query=query, kind=kind, sources=sources, more=more, pages=pages))


def select(video_id, scene_id, candidate_id, actor, *, approve=False, note=""):
    return visual_assets.select(video_id, scene_id, candidate_id, actor,
                                approve=approve, note=note)


def save(video_id, scene_id, candidate_id, actor, *, approve=False, note=""):
    return visual_assets.save_to_library(video_id, scene_id, candidate_id, actor,
                                         approve=approve, note=note)


def regenerate(video_id, scene_id, actor, *, seed=None, prompt=None, negative_prompt=None):
    return visual_assets.regenerate_scene(video_id, scene_id, actor=actor, seed=seed,
                                          prompt=prompt, negative_prompt=negative_prompt)

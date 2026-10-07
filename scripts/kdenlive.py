"""Small, version-pinned Kdenlive/MLT project writer.

The editorial plan remains JSON. This module only lowers an already resolved
timeline to Kdenlive's generation-5 XML shape and renders it through MLT.
"""
import argparse
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET


class KdenliveError(ValueError):
    pass


@dataclass(frozen=True)
class Clip:
    path: Path
    start_frame: int
    duration_frames: int
    source_in: int = 0
    track: str = "video"
    label: str = ""
    gain_db: float = 0.0
    zoom_start: float = 1.0
    zoom_end: float = 1.0
    fade_in_frames: int = 0
    fade_out_frames: int = 0


def _prop(parent, name, value):
    element = ET.SubElement(parent, "property", {"name": name})
    element.text = str(value)
    return element


def _timecode(frames, fps):
    return f"{frames / fps:.3f}"


def _profile(width, height, fps):
    num, den = (fps, 1) if isinstance(fps, int) else fps
    return {"description": f"Content Machine {width}x{height} {num}/{den}",
            "width": str(width), "height": str(height),
            "frame_rate_num": str(num), "frame_rate_den": str(den),
            "sample_aspect_num": "1", "sample_aspect_den": "1",
            "display_aspect_num": str(width), "display_aspect_den": str(height),
            "progressive": "1", "colorspace": "709"}


def _effect(parent, service, identifier, name, start, stop, **properties):
    effect = ET.SubElement(parent, "filter", {"in": str(start), "out": str(stop)})
    for key, value in {"mlt_service": service, "kdenlive:id": identifier,
                       "kdenlive:effectName": name, "kdenlive:sync_in_out": 1,
                       **properties}.items():
        _prop(effect, key, value)
    return effect


def _producer(parent, producer_id, clip, fps, index, width, height, project_dir):
    if not clip.path.is_file():
        raise KdenliveError(f"timeline source does not exist: {clip.path}")
    end = clip.source_in + clip.duration_frames - 1
    producer = ET.SubElement(parent, "producer", {"id": producer_id, "in": "0", "out": str(end)})
    audio = clip.track.startswith("audio")
    properties = {
        "resource": os.path.relpath(clip.path.absolute(), project_dir),
        "mlt_service": "avformat" if audio else "pixbuf",
        "length": end + 1, "eof": "pause", "ttl": end + 1,
        "kdenlive:clipname": clip.label or clip.path.name,
        "kdenlive:id": index, "kdenlive:duration": end + 1,
        "kdenlive:clip_type": 1 if audio else 2,
        "set.test_image": 1 if audio else 0,
        "set.test_audio": 0 if audio else 1,
        "content-machine:sha256": _sha256(clip.path),
        "kdenlive:zone_in": clip.source_in, "kdenlive:zone_out": end,
    }
    for key, value in properties.items():
        _prop(producer, key, value)
    if audio:
        _effect(producer, "volume", "volume", "Volume (keyframable)", clip.source_in, end,
                level=f"{clip.gain_db}dB")
    elif clip.zoom_start != 1 or clip.zoom_end != 1:
        def rect(zoom):
            return f"{width*(1-zoom)/2} {height*(1-zoom)/2} {width*zoom} {height*zoom} 1"
        _effect(producer, "affine", "pan_zoom", "Position and Zoom", clip.source_in, end,
                **{"transition.rect": f"{clip.source_in}={rect(clip.zoom_start)};{end}={rect(clip.zoom_end)}",
                   "transition.distort": 0, "transition.fill": 1,
                   "transition.valign": "middle", "transition.halign": "center",
                   "transition.b_rotate_z": 0, "transition.scale_x": 1,
                   "transition.scale_y": 1, "transition.repeat_off": 1,
                   "transition.mirror_off": 1, "producer.resource": "0x00000000"})
    for incoming, duration in ((True, clip.fade_in_frames), (False, clip.fade_out_frames)):
        if not duration:
            continue
        start = clip.source_in if incoming else end - duration + 1
        stop = start + duration - 1
        if audio:
            _effect(producer, "volume", "fadein" if incoming else "fadeout",
                    "Fade in" if incoming else "Fade out", start, stop,
                    gain=0 if incoming else 1, end=1 if incoming else 0)
        else:
            _effect(producer, "brightness", "fade_from_black" if incoming else "fade_to_black",
                    "Fade in" if incoming else "Fade out", start, stop,
                    level=1, alpha="0=0;-1=1" if incoming else "0=1;-1=0")
    return producer


def _playlist(parent, playlist_id, clips, producers, fps, track):
    playlist = ET.SubElement(parent, "playlist", {"id": playlist_id})
    cursor = 0
    for index, clip in enumerate(clips):
        if clip.track != track:
            continue
        gap = clip.start_frame - cursor
        if gap < 0:
            raise KdenliveError(f"overlapping clips on {track} require an explicit mix: {clip.label}")
        if gap:
            ET.SubElement(playlist, "blank", {"length": str(gap)})
        ET.SubElement(playlist, "entry", {
            "producer": producers[index], "in": str(clip.source_in),
            "out": str(clip.source_in + clip.duration_frames - 1)})
        cursor = clip.start_frame + clip.duration_frames
    return playlist


def _sha256(path):
    import hashlib
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _video_layout(clips):
    """Place an ordered edit on one or two MLT tracks.

    A crossfade is represented by an overlap. MLT cannot overlap entries in a
    single playlist, so adjacent overlapping shots alternate between two video
    tracks and a tractor transition is emitted between them. Longer or
    non-adjacent overlaps are rejected instead of silently changing the edit.
    """
    ordered = sorted(clips, key=lambda clip: (clip.start_frame, clip.label, str(clip.path)))
    if not ordered:
        return [], []
    if all(a.start_frame >= b.start_frame + b.duration_frames
           for a, b in zip(ordered[1:], ordered[:-1])):
        return [ordered], []
    laid_out = []
    transitions = []
    previous = None
    for index, clip in enumerate(ordered):
        if previous is None:
            track = "video0"
        elif clip.start_frame < previous.start_frame + previous.duration_frames:
            overlap = previous.start_frame + previous.duration_frames - clip.start_frame
            if overlap <= 0 or overlap > clip.duration_frames:
                raise KdenliveError("invalid overlapping video clips")
            track = "video1" if previous.track == "video0" else "video0"
        else:
            track = previous.track
        current = replace(clip, track=track)
        if previous is not None and current.start_frame < previous.start_frame + previous.duration_frames:
            overlap = previous.start_frame + previous.duration_frames - current.start_frame
            transitions.append((previous, current, current.start_frame,
                                current.start_frame + overlap - 1))
        laid_out.append(current)
        previous = current
    # A track may not contain two overlapping entries. This catches a
    # three-way overlap before writing a project that MLT would interpret
    # differently from the editorial plan.
    for left, right in zip(sorted(laid_out, key=lambda c: c.start_frame),
                           sorted(laid_out, key=lambda c: c.start_frame)[1:]):
        if left.track == right.track and right.start_frame < left.start_frame + left.duration_frames:
            raise KdenliveError("video edit contains a three-way overlap")
    return [
        [clip for clip in laid_out if clip.track == "video0"],
        [clip for clip in laid_out if clip.track == "video1"],
    ], transitions


def build_project(path, *, width, height, fps, clips, audio_clips, title="Content Machine edit",
                  notes=None):
    """Kdenlive 23.08 native two-playlist track tractors, with editable effects.

    Alternating video tracks crossfade through the upper clip's alpha fades.
    Each audio lane remains independently editable. No source is flattened.
    """
    path = Path(path).absolute()
    video_tracks, transitions = _video_layout(list(clips))
    video = sorted([c for lane in video_tracks for c in lane], key=lambda c: c.start_frame)
    for previous, current, start, stop in transitions:
        # Upper shots fade in over lower shots, then out to the next lower
        # shot. Lower shots retain full alpha so the overlap never dips dark.
        upper = previous if previous.track == "video1" else current
        ix = next(i for i,c in enumerate(video) if c.path == upper.path and c.start_frame == upper.start_frame)
        video[ix] = replace(video[ix], **{
            "fade_out_frames" if upper is previous else "fade_in_frames": stop-start+1})
    audio = list(audio_clips)
    all_clips = audio + video
    if not video or not isinstance(fps, int) or fps <= 0:
        raise KdenliveError("an explicit video timeline and positive integer fps are required")
    if any(c.duration_frames <= 0 or c.start_frame < 0 or c.source_in < 0 for c in all_clips):
        raise KdenliveError("clip positions and durations must be nonnegative")
    end = max(c.start_frame+c.duration_frames for c in all_clips)
    # Keep resources relative to the project directory.  An absolute MLT root
    # would make a bundle silently resolve to the VPS library after relocation.
    root = ET.Element("mlt", {"LC_NUMERIC": "C", "version": "7.22.0",
                             "producer": "main_bin", "root": "."})
    ET.SubElement(root, "profile", _profile(width,height,fps))
    black = ET.SubElement(root,"producer",{"id":"black_track","in":"0","out":str(end-1)})
    for k,v in {"resource":"0", "mlt_service":"color", "mlt_image_format":"rgba", "length":end}.items():
        _prop(black,k,v)
    producers = []
    for i,clip in enumerate(all_clips):
        producers.append(f"producer{i}")
        _producer(root,producers[-1],clip,fps,i+1,width,height,path.parent)
    lanes = sorted({c.track for c in audio}) + sorted({c.track for c in video})
    for n,lane in enumerate(lanes):
        laneclips = [c for c in all_clips if c.track == lane]
        cursor = 0
        for c in sorted(laneclips,key=lambda c:c.start_frame):
            if c.start_frame < cursor:
                raise KdenliveError(f"overlapping clips on track {lane}")
            cursor = c.start_frame+c.duration_frames
        playlist = _playlist(root,f"playlist{2*n}",all_clips,producers,fps,lane)
        for c,entry in zip(laneclips,playlist.findall("entry")):
            _prop(entry,"kdenlive:id",all_clips.index(c)+1)
        ET.SubElement(root,"playlist",{"id":f"playlist{2*n+1}"})
        tractor = ET.SubElement(root,"tractor",{"id":f"tractor{n}","in":"0","out":str(end-1)})
        is_audio = lane.startswith("audio")
        for k,v in {"kdenlive:track_name":lane,"kdenlive:audio_track":int(is_audio),
                    "kdenlive:trackheight":100,"kdenlive:timeline_active":1}.items(): _prop(tractor,k,v)
        for j in (0,1):
            ET.SubElement(tractor,"track",{"producer":f"playlist{2*n+j}","hide":"video" if is_audio else "audio"})
    sequence_id = "{"+str(uuid.uuid4())+"}"
    sequence = ET.SubElement(root,"tractor",{"id":"sequence","in":"0","out":str(end-1)})
    for k,v in {"kdenlive:uuid":sequence_id,"kdenlive:id":len(all_clips)+1,
                "kdenlive:clipname":title,"kdenlive:producer_type":17,
                "kdenlive:sequenceproperties.activeTrack":len(lanes)-1,
                "kdenlive:sequenceproperties.tracks":len(lanes),
                "kdenlive:sequenceproperties.hasAudio":int(bool(audio)),
                "kdenlive:sequenceproperties.hasVideo":1,
                "kdenlive:sequenceproperties.zonein":0,"kdenlive:sequenceproperties.zoneout":end,
                "kdenlive:sequenceproperties.duration":end,
                "kdenlive:sequenceproperties.maxduration":end,
                "kdenlive:sequenceproperties.position":int(fps*5)}.items(): _prop(sequence,k,v)
    ET.SubElement(sequence,"track",{"producer":"black_track"})
    for n,lane in enumerate(lanes):
        ET.SubElement(sequence,"track",{"producer":f"tractor{n}","hide":"video" if lane.startswith("audio") else "audio"})
    for n,lane in enumerate(lanes):
        t=ET.SubElement(sequence,"transition",{"in":"0","out":str(end-1)})
        is_audio=lane.startswith("audio")
        for k,v in {"a_track":0,"b_track":n+1,"mlt_service":"mix" if is_audio else "frei0r.cairoblend",
                    "internal_added":237,"always_active":1,**({"sum":1} if is_audio else {})}.items(): _prop(t,k,v)
    main=ET.SubElement(root,"playlist",{"id":"main_bin"})
    for k,v in {"xml_retain":1,"kdenlive:docproperties.version":"1.1","kdenlive:docproperties.documentid":"1720000000000",
                "kdenlive:docproperties.kdenliveversion":"23.08.5",
                "kdenlive:docproperties.activeTimeline":sequence_id,
                "kdenlive:docproperties.opensequences":sequence_id,
                "kdenlive:docproperties.groups":"[]",
                "kdenlive:documentnotes":notes or ""}.items(): _prop(main,k,v)
    for producer_id,clip in zip(producers,all_clips):
        ET.SubElement(main,"entry",{"producer":producer_id,"in":"0","out":str(clip.source_in+clip.duration_frames-1)})
    ET.SubElement(main,"entry",{"producer":"sequence","in":"0","out":str(end-1)})
    final=ET.SubElement(root,"tractor",{"id":"tractor_project","in":"0","out":str(end-1)})
    _prop(final,"kdenlive:projectTractor",1)
    ET.SubElement(final,"track",{"producer":"sequence","in":"0","out":str(end-1)})
    path.parent.mkdir(parents=True,exist_ok=True)
    ET.indent(root,space="  ")
    ET.ElementTree(root).write(path,encoding="utf-8",xml_declaration=True)
    return {"path":str(path),"duration_frames":end,"duration_seconds":end/fps,
            "clip_count":len(all_clips),"format":"kdenlive-gen5","project_version":"1.1"}


def verify_project(path):
    """Preflight sources and native structure; editor roundtrip is separate evidence."""
    path = Path(path).absolute()
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise KdenliveError(f"invalid project XML: {exc}") from exc
    main = root.find("playlist[@id='main_bin']")
    if main is None or main.find("property[@name='xml_retain']") is None:
        raise KdenliveError("native project bin must be retained by MLT")
    checked = 0
    for producer in root.findall("producer"):
        props = {p.get("name"): p.text for p in producer.findall("property")}
        identity = props.get("content-machine:sha256")
        if identity:
            source = path.parent / props["resource"]
            if not source.is_file() or _sha256(source) != identity:
                raise KdenliveError(f"missing or changed timeline source: {source}")
            checked += 1
    sequences = [t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None]
    if len(sequences) != 1:
        raise KdenliveError("expected one native sequence")
    sequence = sequences[0]
    for lane in sequence.findall("track")[1:]:
        track = root.find(f"tractor[@id='{lane.get('producer')}']")
        if track is None or len(track.findall("track")) != 2:
            raise KdenliveError("native timeline lanes require two playlists")
    if not shutil.which("melt"):
        raise KdenliveError("melt is not installed")
    for category, name in (("producer", "pixbuf"), ("transition", "frei0r.cairoblend"),
                           ("filter", "affine"), ("filter", "brightness"), ("filter", "volume")):
        probe = subprocess.run(["melt", "-query", f"{category}={name}"],capture_output=True,text=True,timeout=20)
        if f"identifier: {name}" not in probe.stdout + probe.stderr:
            raise KdenliveError(f"required MLT plugin unavailable: {name}")
    return {"verified": True, "method": "native structure, source hashes and required MLT plugins",
            "sources_checked": checked, "native_editor_roundtrip": "separate acceptance evidence required"}


def render_project(path, output):
    path, output = Path(path).absolute(), Path(output).absolute()
    verification = verify_project(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    root = ET.parse(path).getroot()
    profile = root.find("profile")
    fps = int(profile.get("frame_rate_num")) / int(profile.get("frame_rate_den"))
    frames = int(root.findall("tractor")[-1].get("out")) + 1
    with tempfile.TemporaryDirectory(prefix=".mlt-render-", dir=output.parent) as temporary:
        pending = Path(temporary) / "review.mp4"
        result = subprocess.run(["melt", str(path), "-consumer", f"avformat:{pending}",
                                 "f=mp4", "vcodec=libx264", "acodec=aac", "preset=medium",
                                 "crf=20", "pix_fmt=yuv420p", "threads=2", "real_time=-2",
                                 "movflags=+faststart", "ar=48000", "ab=192k"],
                                capture_output=True, text=True, timeout=3600,
                                cwd=path.parent)
        if result.returncode or not pending.is_file():
            raise KdenliveError(result.stderr[-4000:] or result.stdout[-4000:])
        probe = subprocess.run(["ffprobe","-v","error","-show_streams","-show_format",
                                "-of","json",str(pending)],capture_output=True,text=True,check=True)
        info = json.loads(probe.stdout)
        if {s["codec_type"] for s in info["streams"]} != {"audio","video"}:
            raise KdenliveError("render must contain audio and video")
        if abs(float(info["format"]["duration"])-frames/fps) > 1/fps + .05:
            raise KdenliveError("render duration differs from the timeline")
        os.replace(pending, output)
    return {"output": str(output), "bytes": output.stat().st_size,
            "duration_seconds": float(info["format"]["duration"]), "preflight": verification}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--render", type=Path)
    args = parser.parse_args()
    try:
        result = verify_project(args.project) if args.verify else render_project(args.project, args.render) if args.render else {"project": str(args.project)}
        print(json.dumps(result, indent=2))
    except (KdenliveError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Kdenlive error: {exc}\n")


if __name__ == "__main__":
    main()

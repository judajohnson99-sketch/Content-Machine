#!/usr/bin/env python3
"""A labelled dataset, and a harness that measures a vision model against it.

``vision.py`` turns a model's reply into findings, and ships thresholds that
treat every model as uncalibrated: only prompt match and plainly severe
artifacts may ask for a look, and even those say "not yet calibrated". This
module is how a model earns more trust - or fails to - on a specific machine.

**The dataset** (``calibration/vision``) is built from the production plates
already in this repository (``projects/exp-*/images``), with subjects drawn
at known positions and defects injected by known filters, so every label is
true by construction: this image shows a red circle on the left; that one has
a watermark; this one is cut off by the frame. 82 images, four groups
(prompt match / artifacts / composition / consistency sets); labels were
checked against contact sheets by eye, and the defect types that did not show
honestly (banding, oversaturation) were left out rather than mislabelled. It is committed,
with a sha256 per image, so it cannot drift silently.

**What it can and cannot establish.** Shapes on dark plates are an easier,
different task from real ComfyUI renders, and the labels were made by the
person who made the images, not a reviewer. A model that scores well here has
shown it can tell a matching image from a mismatched one, does not invent
defects on clean images, and answers in the required format. It has *not*
shown it is accurate on production imagery. ``import-real`` is the path to
that: render real images, label them by eye, and the harness reports those
numbers separately.

**Criteria** (``CRITERIA``) decide, per dimension, whether a model's scores are
trustworthy enough to lift the "not yet calibrated" cap. ``run --apply`` writes
the result into ``config/vision_thresholds.json`` for that model alone and only
for the dimensions that met them. Nothing here runs on its own and nothing
here is paid: it needs a local Ollama (``QUALITY_VISION_URL``) and, for the
GPU worker, the lease from ``gpu_coordination``.

    python3 scripts/vision_calibration.py build        # regenerate the dataset
    python3 scripts/vision_calibration.py verify       # hashes still match
    python3 scripts/vision_calibration.py sheet        # contact sheets to eyeball
    python3 scripts/vision_calibration.py run --model qwen3-vl:2b [--apply]
    python3 scripts/vision_calibration.py label-template <project-id>
    python3 scripts/vision_calibration.py import-real labels.json --reviewer NAME
"""
import argparse
import hashlib
import json
import logging
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quality  # noqa: E402
import vision  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("vision_calibration")

ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT / "calibration" / "vision"
DATASET_VERSION = 1
WIDTH, HEIGHT = 384, 216

# Existing production plates (tracked in git) the scenes are drawn on.
SOURCE_PLATES = {
    "night": "projects/exp-b-sleep-brown/images/deep-night_01.png",
    "slate": "projects/exp-a-rain-focus/images/storm-slate_01.png",
    "amber": "projects/exp-c-narrated-sleep/images/dust-archive_01.png",
}

# What a model has to show, per dimension, before its scores stop being capped.
CRITERIA = {
    "parse_rate": 0.90,            # replies in the required format
    "min_examples": 5,             # per class, per dimension
    "adherence_auc": 0.80,         # matching images outscore mismatched ones
    "composition_auc": 0.75,
    "false_alarm": 0.15,           # clean/good images flagged
    "artifact_min_examples": 3,    # per named artifact, to say anything about it
    "artifact_precision": 0.70,
    "artifact_recall": 0.50,
    "artifact_clean_fpr": 0.15,    # any artifact named on a clean image
    "consistency_recall": 0.66,    # odd-one-out images found
    "consistency_false_alarm": 0.10,
}

LIMITS = (
    "The constructed dataset is simple shapes on dark plates with labels made by the person "
    "who made the images; it shows a model is not obviously broken, not that it is accurate "
    "on production imagery.",
    "Prompt-match and composition are judged on the 1-5 scores the model gives; a threshold "
    "chosen here tolerates a 15% false-alarm rate on good images, which on a long video is "
    "still several findings to look at.",
    "Calibration applies to the model name, the rubric version and the prompt in vision.py; "
    "changing any of them invalidates it.",
)

COLOURS = {"red": (220, 40, 40), "green": (40, 170, 70), "yellow": (230, 200, 40),
           "blue": (50, 90, 220), "white": (235, 235, 235), "orange": (235, 130, 30)}

PLACES = {"left": (96, 108), "right": (288, 108), "centre": (192, 108),
          "top": (192, 54), "bottom": (192, 162)}
PLACE_PHRASE = {"left": "on the left", "right": "on the right", "centre": "in the centre",
                "top": "near the top", "bottom": "near the bottom"}
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top", "centre": "top"}
SIZES = {"large": 50, "small": 26}

# --------------------------------------------------------------------------
# scene description -> ffmpeg
# --------------------------------------------------------------------------


def shape(kind, colour, place, size):
    cx, cy = PLACES[place]
    return {"kind": kind, "colour": colour, "place": place, "size": size,
            "cx": cx, "cy": cy, "r": SIZES[size]}


def raw_shape(kind, colour, cx, cy, r):
    return {"kind": kind, "colour": colour, "place": None, "size": None, "cx": cx, "cy": cy, "r": r}


def describe(shapes):
    parts = [f"a {s['size']} {s['colour']} {s['kind']} {PLACE_PHRASE[s['place']]}" for s in shapes]
    return " and ".join(parts) + " on a dark background"


def _geq_circle(s):
    r, g, b = COLOURS[s["colour"]]
    inside = f"lt(hypot(X-{s['cx']},Y-{s['cy']}),{s['r']})"
    return (f"geq=r='if({inside},{r},r(X,Y))':g='if({inside},{g},g(X,Y))':"
            f"b='if({inside},{b},b(X,Y))'")


def _box(s):
    r, g, b = COLOURS[s["colour"]]
    return (f"drawbox=x={s['cx'] - s['r']}:y={s['cy'] - s['r']}:w={2 * s['r']}:h={2 * s['r']}:"
            f"color=0x{r:02x}{g:02x}{b:02x}:t=fill")


def scene_filter(shapes, post=None):
    chain = [f"scale={WIDTH}:{HEIGHT}:flags=area", "format=rgb24"]
    for s in shapes:
        chain.append(_geq_circle(s) if s["kind"] == "circle" else _box(s))
    if post:
        chain.append(post)
    return ",".join(chain)


def render(plate_key, shapes, out_path, post=None):
    source = ROOT / SOURCE_PLATES[plate_key]
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-frames:v", "1",
         "-vf", scene_filter(shapes, post), "-q:v", "4", str(out_path)], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"could not render {out_path.name}: {result.stderr.decode()[:300]}")


# --------------------------------------------------------------------------
# the dataset definition
# --------------------------------------------------------------------------

ADHERENCE_SCENES = (
    ("night", [("circle", "red", "left", "large")], "colour"),
    ("slate", [("square", "yellow", "right", "large")], "missing"),
    ("amber", [("circle", "blue", "centre", "large")], "shape"),
    ("night", [("square", "green", "top", "small")], "position"),
    ("slate", [("circle", "white", "bottom", "small")], "colour"),
    ("amber", [("square", "red", "left", "large"), ("circle", "yellow", "right", "small")], "missing"),
    ("night", [("circle", "orange", "right", "large")], "position"),
    ("slate", [("circle", "green", "left", "large"), ("square", "blue", "right", "large")], "colour"),
    ("amber", [("square", "white", "centre", "small")], "shape"),
    ("night", [("circle", "yellow", "top", "large")], "missing"),
)

TEXT_POST = ("drawtext=text='SAMPLE watermark.com':fontcolor=white:fontsize=20:"
             "x=w-tw-10:y=h-th-10:box=1:boxcolor=black@0.55")
INJECTIONS = (
    ("text_or_watermark", TEXT_POST, 5),
    ("blur", "boxblur=10:2", 4),
    ("blocky_compression", f"scale=24:14:flags=area,scale={WIDTH}:{HEIGHT}:flags=neighbor", 4),
    ("noise", "noise=alls=70:allf=t", 3),
)
# Named in the vocabulary but with no honest way to inject them on these plates
# (banding and oversaturation did not show on near-black backgrounds; the
# anatomy, duplication, geometry and smearing defects belong to generated
# pictures). A model is therefore never calibrated for them from this dataset.
NOT_COVERED = tuple(a for a in vision.ARTIFACT_VOCABULARY
                    if a not in {n for n, _, _ in INJECTIONS})

GOOD_COMPOSITIONS = (
    ("centre", raw_shape("circle", "red", 192, 108, 50)),
    ("left third", raw_shape("square", "yellow", 128, 108, 46)),
    ("right third", raw_shape("circle", "white", 256, 108, 44)),
    ("upper third", raw_shape("square", "blue", 192, 78, 42)),
    ("centre small", raw_shape("circle", "green", 192, 108, 30)),
)
POOR_COMPOSITIONS = (
    ("cut off by the left edge", raw_shape("circle", "red", 8, 108, 52)),
    ("tiny in the corner", raw_shape("square", "yellow", 22, 22, 9)),
    ("huge and clipped", raw_shape("circle", "white", 192, 108, 170)),
    ("cut off by the right edge", raw_shape("square", "blue", 380, 120, 50)),
    ("cut off by the corner", raw_shape("circle", "green", 384, 216, 60)),
)

CONSISTENCY_SETS = (
    ("A", "night", ("blue", "white")),
    ("B", "amber", ("orange", "yellow")),
    ("C", "slate", ("green", "white")),
    ("D", "night", ("red", "yellow")),
    ("E", "slate", ("blue", "orange")),
    ("F", "amber", ("green", "white")),
)
ODD_POST = "hue=h=150:s=1.8,eq=brightness=0.1"


def _mismatch(shapes, kind):
    shapes = [dict(s) for s in shapes]
    first = shapes[0]
    if kind == "colour":
        others = [c for c in COLOURS if c != first["colour"]]
        first["colour"] = others[(sum(map(ord, first["colour"]))) % len(others)]
        return shapes
    if kind == "missing":
        return shapes[:-1] if len(shapes) > 1 else []
    if kind == "shape":
        first["kind"] = "square" if first["kind"] == "circle" else "circle"
        return shapes
    if kind == "position":
        moved = shape(first["kind"], first["colour"], OPPOSITE[first["place"]], first["size"])
        return [moved] + shapes[1:]
    raise ValueError(kind)


def _case(case_id, group, intent, labels, file):
    return {"id": case_id, "file": file, "source": "constructed", "group": group,
            "intent": intent, "labels": labels}


def _labels(adherence=None, mismatch_kind=None, composition=None, artifacts=(),
            style_set=None, odd=None):
    return {"adherence": adherence, "mismatch_kind": mismatch_kind, "composition": composition,
            "artifacts": list(artifacts), "style_set": style_set, "odd_one_out": odd}


def build_cases(images_dir):
    """Render every image and return the manifest's case list, in order."""
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    cases = []

    def add(case_id, group, intent, labels, plate, shapes, post=None):
        name = f"{case_id}.jpg"
        render(plate, shapes, images_dir / name, post)
        cases.append(_case(case_id, group, intent, labels, f"images/{name}"))

    for i, (plate, spec, kind) in enumerate(ADHERENCE_SCENES, start=1):
        shapes = [shape(*s) for s in spec]
        intent = describe(shapes)
        add(f"adh-{i:02d}-match", "adherence", intent, _labels("match"), plate, shapes)
        add(f"adh-{i:02d}-mismatch", "adherence", intent,
            _labels("mismatch", kind), plate, _mismatch(shapes, kind))

    rng = random.Random(2026)
    bases = [(p, [shape(*s) for s in spec]) for p, spec, _ in ADHERENCE_SCENES]
    for name, post, count in INJECTIONS:
        # Without replacement: two cases that are the same image and prompt
        # would be counted as independent evidence.
        for n, index in enumerate(rng.sample(range(len(bases)), count), start=1):
            plate, shapes = bases[index]
            add(f"art-{name.split('_')[0]}-{n:02d}", "artifacts", describe(shapes),
                _labels(artifacts=[name]), plate, shapes, post)

    plates = ["night", "slate", "amber"]
    for n, (what, s) in enumerate(GOOD_COMPOSITIONS, start=1):
        add(f"cmp-good-{n:02d}", "composition", f"a {s['colour']} {s['kind']} on a dark background",
            _labels(composition="good"), plates[n % 3], [s])
    for n, (what, s) in enumerate(POOR_COMPOSITIONS, start=1):
        add(f"cmp-poor-{n:02d}", "composition", f"a {s['colour']} {s['kind']} on a dark background",
            _labels(composition="poor"), plates[n % 3], [s])

    for tag, plate, palette in CONSISTENCY_SETS:
        used = set()
        for n in range(1, 6):
            while True:
                spec = (rng.choice(("circle", "square")), rng.choice(palette),
                        rng.choice(list(PLACES)), rng.choice(("large", "small")))
                if spec not in used:
                    used.add(spec)
                    break
            shapes = [shape(*spec)]
            add(f"set{tag}-{n}", "consistency", describe(shapes),
                _labels(style_set=tag, odd=False), plate, shapes)
        shapes = [shape("circle", palette[0], "centre", "large")]
        add(f"set{tag}-odd", "consistency", describe(shapes),
            _labels(style_set=tag, odd=True), plate, shapes, ODD_POST)
    return cases


def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _ffmpeg_version():
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout
    return out.splitlines()[0] if out else "unknown"


def build(directory=None):
    """Render the dataset into ``directory`` and write its manifest."""
    directory = Path(directory or DATASET_DIR)
    images = directory / "images"
    if images.exists():
        shutil.rmtree(images)
    cases = build_cases(images)
    for case in cases:
        case["sha256"] = _sha256(directory / case["file"])
    manifest = {
        "dataset_version": DATASET_VERSION,
        "label_source": "constructed",
        "human_reviewed": False,
        "review_status": "labels are true by construction and were checked against contact sheets "
                         "by whoever built the set; no independent human has reviewed them. Use "
                         "`sheet` to review, and `import-real` for real renders.",
        "note": "Subjects drawn at known positions on production plates, defects injected by "
                "known filters. See scripts/vision_calibration.py for what this can and cannot show.",
        "image_size": [WIDTH, HEIGHT],
        "source_plates": {key: {"path": path, "sha256": _sha256(ROOT / path)}
                          for key, path in SOURCE_PLATES.items()},
        "built_with": _ffmpeg_version(),
        "counts": {group: sum(1 for c in cases if c["group"] == group)
                   for group in ("adherence", "artifacts", "composition", "consistency")},
        "cases": cases,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load_manifest(directory=None):
    directory = Path(directory or DATASET_DIR)
    return json.loads((directory / "manifest.json").read_text())


def load_cases(directory=None):
    """Constructed cases plus any real, human-labelled ones, each with its path."""
    directory = Path(directory or DATASET_DIR)
    cases = [dict(c, path=directory / c["file"]) for c in load_manifest(directory)["cases"]]
    real = directory / "real" / "manifest.json"
    if real.is_file():
        cases += [dict(c, path=directory / "real" / c["file"])
                  for c in json.loads(real.read_text()).get("cases", [])]
    return cases


def verify(directory=None):
    """Problems with the dataset on disk; an empty list means it is intact."""
    problems = []
    try:
        cases = load_cases(directory)
    except (OSError, ValueError) as e:
        return [f"manifest unreadable: {e}"]
    seen = set()
    for case in cases:
        if case["id"] in seen:
            problems.append(f"{case['id']}: duplicate id")
        seen.add(case["id"])
        path = case["path"]
        if not path.is_file():
            problems.append(f"{case['id']}: missing {case['file']}")
        elif _sha256(path) != case.get("sha256"):
            problems.append(f"{case['id']}: {case['file']} changed since it was labelled")
        labels = case.get("labels") or {}
        if labels.get("adherence") not in ("match", "mismatch", None):
            problems.append(f"{case['id']}: bad adherence label {labels.get('adherence')!r}")
        if labels.get("composition") not in ("good", "poor", None):
            problems.append(f"{case['id']}: bad composition label {labels.get('composition')!r}")
        unknown = [a for a in labels.get("artifacts") or [] if a not in vision.ARTIFACT_VOCABULARY]
        if unknown:
            problems.append(f"{case['id']}: artifact label(s) outside the vocabulary: {unknown}")
    return problems


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def auc(good, bad):
    """Probability a good example outscores a bad one (ties count half)."""
    if not good or not bad:
        return None
    wins = sum((g > b) + 0.5 * (g == b) for g in good for b in bad)
    return wins / (len(good) * len(bad))


def best_threshold(good, bad, max_false_alarm):
    """The score at or below which an image is flagged.

    Highest catch rate whose false-alarm rate on good examples stays within
    ``max_false_alarm``; ``None`` when no threshold catches anything within it.
    """
    best = None
    for t in range(1, 5):
        caught = sum(1 for b in bad if b <= t) / len(bad)
        false = sum(1 for g in good if g <= t) / len(good)
        if false <= max_false_alarm and caught > 0 and (best is None or caught > best["caught"]):
            best = {"threshold": t, "caught": round(caught, 3), "false_alarm": round(false, 3)}
    return best


def _score_dimension(name, good, bad, auc_floor):
    if len(good) < CRITERIA["min_examples"] or len(bad) < CRITERIA["min_examples"]:
        return {"examples": {"good": len(good), "bad": len(bad)}, "meets_criteria": False,
                "reason": f"fewer than {CRITERIA['min_examples']} judged examples in a class"}
    score = auc(good, bad)
    pick = best_threshold(good, bad, CRITERIA["false_alarm"])
    meets = score >= auc_floor and pick is not None
    reason = ("meets the criteria" if meets else
              f"AUC {score:.2f} is under {auc_floor}" if score < auc_floor else
              f"no threshold catches mismatches within a {CRITERIA['false_alarm']:.0%} false-alarm rate")
    return {"examples": {"good": len(good), "bad": len(bad)}, "auc": round(score, 3),
            "mean_good": round(sum(good) / len(good), 2), "mean_bad": round(sum(bad) / len(bad), 2),
            "suggested": pick, "meets_criteria": meets, "reason": reason}


def _artifact_dimension(cases, judged):
    clean = [c for c in cases if c["id"] in judged and not c["labels"]["artifacts"]]
    named_on_clean = sum(1 for c in clean if judged[c["id"]]["artifacts"]["found"])
    clean_fpr = (named_on_clean / len(clean)) if clean else None
    per_name, passing, failed = {}, [], []
    for name in vision.ARTIFACT_VOCABULARY:
        positives = [c for c in cases if c["id"] in judged and name in c["labels"]["artifacts"]]
        if len(positives) < CRITERIA["artifact_min_examples"]:
            continue
        hits = sum(1 for c in positives if name in judged[c["id"]]["artifacts"]["found"])
        flagged = [c for c in cases if c["id"] in judged and name in judged[c["id"]]["artifacts"]["found"]]
        true_pos = sum(1 for c in flagged if name in c["labels"]["artifacts"])
        recall = hits / len(positives)
        precision = (true_pos / len(flagged)) if flagged else 0.0
        ok = precision >= CRITERIA["artifact_precision"] and recall >= CRITERIA["artifact_recall"]
        per_name[name] = {"examples": len(positives), "recall": round(recall, 3),
                          "precision": round(precision, 3), "meets_criteria": ok}
        if name in vision.SEVERE_ARTIFACTS:
            (passing if ok else failed).append(name)
    enough = clean_fpr is not None and len(clean) >= CRITERIA["min_examples"]
    meets = enough and clean_fpr <= CRITERIA["artifact_clean_fpr"] and bool(passing)
    return {"clean_examples": len(clean), "clean_false_positive_rate":
            round(clean_fpr, 3) if clean_fpr is not None else None,
            "per_artifact": per_name, "may_warn": sorted(passing),
            "measured_but_unreliable": sorted(failed), "not_covered_by_dataset": list(NOT_COVERED),
            "meets_criteria": meets,
            "reason": ("meets the criteria" if meets else
                       "too few clean examples judged" if not enough else
                       f"names an artifact on {clean_fpr:.0%} of clean images "
                       f"(limit {CRITERIA['artifact_clean_fpr']:.0%})"
                       if clean_fpr > CRITERIA["artifact_clean_fpr"] else
                       "no severe artifact is both detected and precise enough to warn on")}


def _consistency_dimension(cases, judged):
    sets = {}
    for c in cases:
        tag = c["labels"].get("style_set")
        if tag and c["id"] in judged:
            sets.setdefault(tag, []).append(c)
    if not sets:
        return {"meets_criteria": False, "reason": "no consistency sets were judged"}
    odd_total = sum(1 for cs in sets.values() for c in cs if c["labels"]["odd_one_out"])
    uniform_total = sum(1 for cs in sets.values() for c in cs if not c["labels"]["odd_one_out"])
    if odd_total < 2:
        return {"meets_criteria": False, "reason": "fewer than 2 odd-one-out images were judged"}
    # One fixed rule, not a search: with a handful of odd-one-out images a grid
    # of settings would find one that passes by chance, which is exactly the
    # kind of flattering result this harness exists to avoid.
    rule = dict(vision.DEFAULT_THRESHOLDS["consistency"])
    found, wrong = 0, 0
    for cs in sets.values():
        items = [({"scene_ids": [c["id"]], "path": c["path"], "start": None},
                  judged[c["id"]]) for c in cs]
        findings, _ = quality.consistency_findings(
            items, lambda model: dict(vision.thresholds_for(model), consistency=rule))
        flagged = {f["scene_ids"][0] for f in findings}
        odd = {c["id"] for c in cs if c["labels"]["odd_one_out"]}
        found += len(flagged & odd)
        wrong += len(flagged - odd)
    recall, false_alarm = found / odd_total, wrong / uniform_total
    ok = recall >= CRITERIA["consistency_recall"] and false_alarm <= CRITERIA["consistency_false_alarm"]
    suggested = {"rule": rule, "recall": round(recall, 3), "false_alarm": round(false_alarm, 3)}
    return {"odd_examples": odd_total, "uniform_examples": uniform_total,
            "measured": suggested, "suggested": suggested if ok else None, "meets_criteria": ok,
            "reason": "meets the criteria" if ok else
                      f"finds {found} of {odd_total} odd images and flags {wrong} of "
                      f"{uniform_total} uniform ones (needs recall "
                      f"{CRITERIA['consistency_recall']:.0%}, false alarms at most "
                      f"{CRITERIA['consistency_false_alarm']:.0%})"}


def evaluate(cases, result, model, seconds=None):
    """Measure one model's judgements against the labels."""
    judged = result.judgements
    ids = {c["id"] for c in cases}
    by_id = {c["id"]: c for c in cases}
    judged = {k: v for k, v in judged.items() if k in ids}
    reasons = result.reasons() if hasattr(result, "reasons") else []
    parse_rate = len(judged) / len(cases) if cases else 0.0

    def scores(dimension, label_key, good_label, bad_label, pool=None):
        good, bad = [], []
        for c in (pool or cases):
            if c["id"] not in judged:
                continue
            label = c["labels"].get(label_key)
            score = judged[c["id"]][dimension]["score"]
            (good if label == good_label else bad if label == bad_label else []).append(score)
        return good, bad

    adherence = _score_dimension("adherence", *scores("adherence", "adherence", "match", "mismatch"),
                                 CRITERIA["adherence_auc"])
    composition = _score_dimension("composition", *scores("composition", "composition", "good", "poor"),
                                   CRITERIA["composition_auc"])
    artifacts = _artifact_dimension(cases, judged)
    consistency = _consistency_dimension(cases, judged)
    dims = {"adherence": adherence, "composition": composition,
            "artifacts": artifacts, "consistency": consistency}
    reliable = parse_rate >= CRITERIA["parse_rate"]
    calibrated = [d for d in vision.DIMENSIONS if reliable and dims[d]["meets_criteria"]]

    by_source = {}
    for source in sorted({c.get("source", "constructed") for c in cases}):
        pool = [c for c in cases if c.get("source", "constructed") == source]
        g, b = scores("adherence", "adherence", "match", "mismatch", pool)
        by_source[source] = {"cases": len(pool), "judged": sum(1 for c in pool if c["id"] in judged),
                             "adherence_auc": round(auc(g, b), 3) if auc(g, b) is not None else None}
    times = [j["seconds"] for j in judged.values() if j.get("seconds") is not None]
    thresholds = {"calibrated_dimensions": calibrated}
    if "adherence" in calibrated:
        pick = adherence["suggested"]
        thresholds["adherence"] = {"warn_at_or_below": pick["threshold"],
                                   "note_at_or_below": min(pick["threshold"] + 1, 4)}
    if "composition" in calibrated:
        pick = composition["suggested"]
        thresholds["composition"] = {"warn_at_or_below": pick["threshold"],
                                     "note_at_or_below": min(pick["threshold"] + 1, 4)}
    if "artifacts" in calibrated:
        measured = set(artifacts["may_warn"]) | set(artifacts["measured_but_unreliable"])
        # A severe artifact that was measured and failed loses its warning; one the
        # dataset cannot test keeps it, but is still reported as not calibrated.
        thresholds["artifacts"] = {
            "warn": sorted((vision.SEVERE_ARTIFACTS - measured) | set(artifacts["may_warn"])),
            "calibrated_names": artifacts["may_warn"]}
    if "consistency" in calibrated:
        thresholds["consistency"] = consistency["suggested"]["rule"]
    return {
        "calibration_version": 1, "model": model, "ran_utc": vision.utc_now(),
        "rubric_version": vision.RUBRIC_VERSION,
        "criteria": CRITERIA,
        "reliability": {"cases": len(cases), "judged": len(judged),
                        "parse_rate": round(parse_rate, 3), "meets_criteria": reliable,
                        "not_judged": reasons,
                        "mean_seconds_per_image": round(sum(times) / len(times), 2) if times else None,
                        "total_seconds": round(seconds, 1) if seconds is not None else None},
        "dimensions": dims, "by_source": by_source,
        "calibrated_dimensions": calibrated, "proposed_thresholds": thresholds,
        "limits": list(LIMITS),
    }


def apply_to_thresholds(report, thresholds_path=None, result_file=None):
    """Write the model's calibrated dimensions into the thresholds file.

    Does nothing (and returns ``None``) unless at least one dimension met its
    criteria; never touches another model's entry.
    """
    dims = report["calibrated_dimensions"]
    if not dims:
        return None
    path = Path(thresholds_path or vision.THRESHOLDS_PATH)
    config = vision.load_thresholds(path)
    entry = dict(report["proposed_thresholds"])
    manifest_counts = report["reliability"]["cases"]
    entry.update({
        "calibrated_on": report["ran_utc"][:10],
        "dataset": {"version": DATASET_VERSION, "cases": manifest_counts,
                    "label_source": "constructed (plus real where listed in the result file)",
                    "human_reviewed": any(s != "constructed" for s in report["by_source"])},
        "rubric_version": report["rubric_version"],
        "result_file": result_file,
        "uncalibrated_dimensions": [d for d in vision.DIMENSIONS if d not in dims],
    })
    config.setdefault("models", {})[report["model"]] = entry
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    tmp.replace(path)
    return entry


# --------------------------------------------------------------------------
# real, human-labelled cases
# --------------------------------------------------------------------------

def label_template(video_id, out=None):
    """A labels file for a project's depicted scene images, to fill in by eye."""
    import storyboard as storyboard_mod
    board = storyboard_mod.load(video_id)
    if board is None:
        raise SystemExit(f"no storyboard for {video_id}")
    pdir = ROOT / "projects" / video_id
    items, seen = [], set()
    for scene in board.get("scenes") or []:
        generation = scene.get("generation") or {}
        image = scene.get("image")
        if not image or image in seen or generation.get("produces_depicted") is not True:
            continue
        seen.add(image)
        items.append({"image": str(pdir / image), "intent": scene.get("image_prompt") or "",
                      "adherence": None, "composition": None, "artifacts": [], "notes": ""})
    if not items:
        raise SystemExit(f"{video_id} has no depicted scene images to label (abstract plates do not count)")
    target = Path(out or ROOT / "calibration" / "vision" / "real" / f"{video_id}.labels.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "instructions": "For each image set adherence to 'match' or 'mismatch' (does it show its "
                        "intent?), composition to 'good' or 'poor', and artifacts to a list from "
                        f"{list(vision.ARTIFACT_VOCABULARY)} (empty if clean). Delete images you "
                        "cannot judge. Then run `import-real`.",
        "images": items}, indent=2) + "\n")
    return target, len(items)


def import_real(labels_file, reviewer, directory=None):
    """Add human-labelled real renders to the dataset's ``real`` split."""
    directory = Path(directory or DATASET_DIR)
    spec = json.loads(Path(labels_file).read_text())
    real = directory / "real"
    (real / "images").mkdir(parents=True, exist_ok=True)
    manifest_path = real / "manifest.json"
    manifest = (json.loads(manifest_path.read_text()) if manifest_path.is_file()
                else {"label_source": "human", "human_reviewed": True, "cases": []})
    known = {c["id"] for c in manifest["cases"]}
    added, problems = 0, []
    for entry in spec.get("images", []):
        src = Path(entry["image"])
        problem = None
        if entry.get("adherence") not in ("match", "mismatch"):
            problem = "adherence must be 'match' or 'mismatch'"
        elif entry.get("composition") not in ("good", "poor"):
            problem = "composition must be 'good' or 'poor'"
        elif not isinstance(entry.get("artifacts"), list) or any(
                a not in vision.ARTIFACT_VOCABULARY for a in entry["artifacts"]):
            problem = "artifacts must be a list from the vocabulary"
        elif not entry.get("intent"):
            problem = "intent is empty"
        elif not src.is_file():
            problem = "image file not found"
        if problem:
            problems.append(f"{src.name}: {problem}")
            continue
        sha = _sha256(src)
        case_id = f"real-{sha[:10]}"
        if case_id in known:
            continue
        target = real / "images" / f"{case_id}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1", "-vf",
                        "scale='min(768,iw)':'min(768,ih)':force_original_aspect_ratio=decrease:flags=area",
                        "-q:v", "3", str(target)], check=True, capture_output=True)
        manifest["cases"].append({
            "id": case_id, "file": f"images/{case_id}.jpg", "sha256": _sha256(target),
            "source": "real", "group": "real", "intent": entry["intent"],
            "labels": _labels(entry["adherence"], None, entry["composition"], entry["artifacts"]),
            "labelled_by": reviewer, "labelled_utc": vision.utc_now(), "notes": entry.get("notes", "")})
        added += 1
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return added, problems


# --------------------------------------------------------------------------
# contact sheets, so the labels can be checked by eye
# --------------------------------------------------------------------------

def sheets(directory=None, out=None):
    directory = Path(directory or DATASET_DIR)
    out = Path(out or directory / "sheets")
    out.mkdir(parents=True, exist_ok=True)
    written = []
    cases = load_cases(directory)
    for group in sorted({c["group"] for c in cases}):
        members = [c for c in cases if c["group"] == group]
        with tempfile.TemporaryDirectory(prefix="cm-sheet-") as tmp:
            for n, case in enumerate(members):
                label = re.sub(r"[^A-Za-z0-9_-]", "", case["id"])
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-y", "-i", str(case["path"]), "-vf",
                     f"scale={WIDTH}:-2,drawtext=text='{label}':fontcolor=yellow:fontsize=16:x=6:y=6:"
                     "box=1:boxcolor=black@0.6", "-q:v", "4", str(Path(tmp) / f"{n:03d}.jpg")],
                    check=True, capture_output=True)
            sheet = out / f"{group}.jpg"
            rows = -(-len(members) // 4)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i",
                            str(Path(tmp) / "%03d.jpg"), "-vf", f"tile=4x{rows}:padding=4:color=black",
                            "-frames:v", "1", "-q:v", "4", str(sheet)], check=True, capture_output=True)
        written.append(sheet)
    return written


# --------------------------------------------------------------------------
# running a model
# --------------------------------------------------------------------------

def run_model(model, cases, runner=None, use_cache=True, coordination=None):
    """Judge every case with ``model`` and evaluate it. Returns the report, or
    a ``{"not_run": reason}`` dict when the model could not be reached."""
    started = datetime.now(timezone.utc)
    if runner is None:
        kwargs = {"models": [model]}
        if not use_cache:
            kwargs["cache"] = vision.Cache(tempfile.mkdtemp(prefix="cm-calib-cache-"))
        if coordination:
            kwargs["coordination"] = coordination
        runner = vision.Runner(**kwargs)
    items = [{"key": c["id"], "path": c["path"], "intent": c["intent"]} for c in cases]
    result = runner.judge_images(items)
    if not result.judgements and result.stopped:
        return {"not_run": result.stopped["detail"], "code": result.stopped["code"]}
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return evaluate(cases, result, model, seconds=elapsed)


def _print_report(report):
    rel = report["reliability"]
    print(f"model {report['model']}: judged {rel['judged']}/{rel['cases']} "
          f"(parse rate {rel['parse_rate']:.0%}, {rel['mean_seconds_per_image']}s/image)")
    for name, dim in report["dimensions"].items():
        mark = "meets criteria" if dim["meets_criteria"] else "does NOT meet criteria"
        extra = f" AUC {dim['auc']}" if "auc" in dim else ""
        print(f"  {name:<12} {mark}{extra} - {dim['reason']}")
    done = report["calibrated_dimensions"]
    print("calibrated for: " + (", ".join(done) if done else "nothing - every dimension stays capped"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build, verify and run the vision calibration dataset.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="regenerate the constructed dataset")
    p.add_argument("--out", default=None)
    p.set_defaults(func=lambda a: (print(json.dumps(build(a.out)["counts"])), 0)[1])
    p = sub.add_parser("verify", help="check the dataset against its recorded hashes")
    p.set_defaults(func=_cmd_verify)
    p = sub.add_parser("sheet", help="write contact sheets for eyeballing the labels")
    p.set_defaults(func=lambda a: ([print(s) for s in sheets()], 0)[1])
    p = sub.add_parser("run", help="measure a model against the dataset")
    p.add_argument("--model", required=True)
    p.add_argument("--url", default=None, help="Ollama endpoint (default: QUALITY_VISION_URL)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--apply", action="store_true",
                   help="write the dimensions that met the criteria into config/vision_thresholds.json")
    p.add_argument("--thresholds", default=None,
                   help="thresholds file for --apply (default: config/vision_thresholds.json)")
    p.add_argument("--results", default=None,
                   help="directory for the report (default: calibration/vision/results)")
    p.set_defaults(func=_cmd_run)
    p = sub.add_parser("label-template", help="a labels file for a project's depicted images")
    p.add_argument("video_id")
    p.set_defaults(func=_cmd_template)
    p = sub.add_parser("import-real", help="add human-labelled real renders to the dataset")
    p.add_argument("labels")
    p.add_argument("--reviewer", required=True)
    p.set_defaults(func=_cmd_import)
    args = parser.parse_args(argv)
    return args.func(args)


def _cmd_verify(args):
    problems = verify()
    for problem in problems:
        print(problem)
    print("dataset intact" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


def _cmd_run(args):
    problems = verify()
    if problems:
        print("refusing to run: the dataset does not match its manifest:\n  " + "\n  ".join(problems))
        return 1
    if args.url:
        import os
        os.environ["QUALITY_VISION_URL"] = args.url
    cases = load_cases()
    report = run_model(args.model, cases, use_cache=not args.no_cache)
    if "not_run" in report:
        print(f"not run: {report['not_run']}")
        return 2
    results = Path(args.results) if args.results else DATASET_DIR / "results"
    results.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", args.model)
    target = results / f"{name}-{report['ran_utc'][:10]}.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    _print_report(report)
    print(f"report: {target}")
    if args.apply:
        try:
            shown = str(target.relative_to(ROOT))
        except ValueError:
            shown = str(target)
        entry = apply_to_thresholds(report, args.thresholds, result_file=shown)
        print("thresholds updated for " + args.model if entry else
              "nothing met the criteria; thresholds left as they were")
    return 0


def _cmd_template(args):
    target, count = label_template(args.video_id)
    print(f"{count} image(s) to label: {target}")
    return 0


def _cmd_import(args):
    added, problems = import_real(args.labels, args.reviewer)
    for problem in problems:
        print(f"skipped {problem}")
    print(f"added {added} real case(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Knowledge layer: the Obsidian vault and its derived Graphify graph.

    ./content-machine knowledge capture --type finding --title "..." ...
    ./content-machine knowledge refresh
    ./content-machine knowledge status
    ./content-machine knowledge query "<question>"
    ./content-machine knowledge context "<task>"
    ./content-machine knowledge open

Two layers, one direction of flow:

    knowledge/            canonical, human-readable, committed  (SOURCE)
      └── graphify-out/   graph.json + graph.html + GRAPH_REPORT.md  (DERIVED)

The vault is the source of truth for durable knowledge - topics, entities,
research findings, decisions, measured constraints. The graph is a lens over
it and is thrown away and rebuilt by `refresh`; nothing may depend on the
graph that is not recoverable from the notes.

The graph is NOT an operational database. Run state lives in
projects/<id>/metadata.json and experiments/concepts.json, and those files
stay authoritative. A note points at them; it does not restate them.

`refresh` uses the claude-cli backend by default: it drives the local Claude
Code session, so it needs no extra Python package and spends nothing through
a paid API. Override with --backend only deliberately.
"""
import argparse
import collections
import contextlib
import fcntl
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("knowledge")

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "knowledge"
GRAPH_DIR = VAULT / "graphify-out"
GRAPH_JSON = GRAPH_DIR / "graph.json"
GRAPH_HTML = GRAPH_DIR / "graph.html"
GRAPH_REPORT = GRAPH_DIR / "GRAPH_REPORT.md"
CAPTURE_DIR = VAULT / "Captured"

DEFAULT_BACKEND = "claude-cli"

# The capture gate. A knowledge artefact is durable and cross-cutting; an
# operational event is neither. Only these types are accepted, and each
# still has to carry evidence - see capture() below.
NOTE_TYPES = {
    "decision": "a choice that was made, and the reasoning that binds future work",
    "finding": "a verified result from research against a citable source",
    "insight": "a conclusion validated against measured data from our own runs",
    "constraint": "a measured limit this pipeline must plan around",
    "entity": "a person, organisation, product or place that recurs across work",
    "topic": "a subject area that groups concepts, research and experiments",
}

# Evidence strength, using the vocabulary already in experiments/concepts.json.
CONFIDENCE = ("VERIFIED", "INFERRED", "UNVERIFIED")

MIN_BODY_CHARS = 120


class KnowledgeError(Exception):
    """Raised with a human-readable reason a knowledge operation cannot proceed."""


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------

def corpus_notes():
    """Every source note in the vault, excluding the derived graph directory."""
    if not VAULT.is_dir():
        return []
    return sorted(
        p for p in VAULT.rglob("*.md")
        if GRAPH_DIR not in p.parents and not any(part.startswith(".") for part in p.parts)
    )


def graph_summary():
    """(nodes, edges, communities) from graph.json, or None if it is not built."""
    if not GRAPH_JSON.exists():
        return None
    data = json.loads(GRAPH_JSON.read_text())
    nodes = data.get("nodes", [])
    communities = {n.get("community") for n in nodes if n.get("community") is not None}
    return len(nodes), len(data.get("links", [])), len(communities)


def is_stale():
    """True when a source note is newer than the graph built from it."""
    if not GRAPH_JSON.exists():
        return True
    graph_mtime = GRAPH_JSON.stat().st_mtime
    return any(p.stat().st_mtime > graph_mtime for p in corpus_notes())


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def slugify(title):
    slug = re.sub(r"[^\w\s-]", "", title).strip()
    slug = re.sub(r"\s+", " ", slug)
    if not slug:
        raise KnowledgeError("title reduces to an empty filename")
    return slug


def _yaml_str(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def capture(*, note_type, title, summary, body, evidence, links=(), tags=(),
            confidence="UNVERIFIED", source_url=None, author=None,
            contributor="content-machine-agent", force=False, vault=None):
    """Persist one knowledge artefact into the vault. Returns the note path.

    The gate is fail-closed and deliberately narrow: an artefact without a
    recognised type, without evidence, or without enough substance to be
    worth re-reading is refused rather than filed. Render logs, QC verdicts
    and job status are operational activity, not knowledge - they belong in
    projects/<id>/metadata.json.

    Frontmatter follows Graphify's own ingest convention (type, author,
    captured_at, contributor, source_url) so a refresh carries this
    provenance straight through onto the graph node.
    """
    if note_type not in NOTE_TYPES:
        raise KnowledgeError(
            f"unknown type {note_type!r}. Knowledge is one of: "
            + ", ".join(sorted(NOTE_TYPES))
        )
    if confidence not in CONFIDENCE:
        raise KnowledgeError(f"confidence must be one of {', '.join(CONFIDENCE)}")
    title = (title or "").strip()
    summary = (summary or "").strip()
    body = (body or "").strip()
    evidence = (evidence or "").strip()
    if not title:
        raise KnowledgeError("a note needs a title")
    if not summary:
        raise KnowledgeError("a note needs a one-line summary")
    if not evidence:
        raise KnowledgeError(
            "a note needs --evidence: the file, run, URL or measurement it "
            "rests on. Knowledge without a source is not capturable."
        )
    if len(body) < MIN_BODY_CHARS:
        raise KnowledgeError(
            f"body is {len(body)} chars; at least {MIN_BODY_CHARS} are required. "
            "If it cannot be explained at that length it is probably "
            "operational activity, not knowledge."
        )

    target_dir = (Path(vault) if vault else VAULT) / "Captured"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{slugify(title)}.md"
    if path.exists() and not force:
        raise KnowledgeError(f"{path} already exists; pass --force to replace it")

    now = datetime.now(timezone.utc).isoformat()
    front = [
        "---",
        f'title: "{_yaml_str(title)}"',
        f"type: {note_type}",
        f"confidence: {confidence}",
        f"captured_at: {now}",
        f'contributor: "{_yaml_str(contributor)}"',
    ]
    if author:
        front.append(f'author: "{_yaml_str(author)}"')
    if source_url:
        front.append(f'source_url: "{_yaml_str(source_url)}"')
    front.append(f'evidence: "{_yaml_str(evidence)}"')
    front.append("tags:")
    for tag in ("knowledge/captured", f"knowledge/{note_type}", *tags):
        front.append(f"  - {tag}")
    front.append("---")

    parts = [
        "\n".join(front),
        "",
        f"# {title}",
        "",
        f"**{summary}**",
        "",
        body,
        "",
        "## Evidence",
        "",
        f"- {evidence}",
    ]
    if source_url:
        parts.append(f"- {source_url}")
    if links:
        parts += ["", "## Related", ""]
        parts += [f"- [[{link}]]" for link in links]
    parts += ["", f"*Captured {now} by {contributor}. Confidence: {confidence}.*", ""]

    text = "\n".join(parts)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------
# context retrieval
# --------------------------------------------------------------------------
#
# One question this answers: "before I start, what do I actually need to
# know?" - without recursively reading the corpus. `query` traverses the
# knowledge graph from a question and returns node names; this ranks the
# whole corpus against a task and returns a budgeted brief of one-line
# summaries plus file pointers, so the agent opens two notes instead of
# twenty and never reads the code vault at all.
#
# Ranking is lexical and dependency-free on purpose. The vault is small
# enough that IDF-weighted overlap beats anything needing an embedding
# service, and the standard-library-only rule the render path lives under is
# worth keeping here too. The graphs are read as plain JSON in-process: no
# `graphify` subprocess, no API call, nothing to spend.

STATE_MD = ROOT / ".paul" / "STATE.md"
PROJECTS_DIR = ROOT / "projects"
CODE_GRAPH_JSON = ROOT / "graphify-out" / "graph.json"

# A character of English is roughly a quarter of a token. Budgeting on
# characters keeps the cap honest without importing a tokeniser.
CHARS_PER_TOKEN = 4
FOOTER_RESERVE_CHARS = 260

# A note reached only through the graph is real context but weaker evidence
# than a direct lexical hit, and must not outrank one.
GRAPH_NEIGHBOUR_SCORE = 0.35

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOPWORDS = frozenset("""
the a an and or of to in for on with without is are was were be been being by
from that this these those it its as at not no do does did how what when which
why who where we our us you your they their run runs running use used using
make makes made get gets got can could should would will just only also then
than there here into out over under about after before all any some more most
""".split())

# Where a term appearing in a note counts for the most. A title match is a
# strong signal; a body match is a weak one, and is capped so a long note
# cannot win on repetition alone.
_FIELD_WEIGHTS = (("title", 3.0), ("tags", 2.0), ("summary", 2.0), ("body", 1.0))
_MAX_FIELD_HITS = 3

_SUMMARY_RE = re.compile(r"\*\*(.+?)\*\*", re.S)


def _tokens(text):
    return [t for t in _TOKEN_RE.findall((text or "").lower())
            if len(t) > 2 and t not in _STOPWORDS]


def _parse_frontmatter(block):
    """Enough YAML for the frontmatter `capture` writes. No dependency."""
    meta, key = {}, None
    for line in block.splitlines():
        if line.startswith(("  - ", "- ")) and key:
            meta.setdefault(key + "_list", []).append(line.split("- ", 1)[1].strip())
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            meta[key] = value.strip().strip('"')
    return meta


def _split_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    return _parse_frontmatter(text[3:end]), text[end + 4:]


def _summary_of(body, limit=220):
    """The one-line claim: the first paragraph after the heading.

    `capture` writes that paragraph fully bold, and the hand-written notes
    follow the same shape, so the bold is stripped when it wraps the whole
    paragraph. Matching a bare bold *run* instead would pick up the first
    emphasised term in a note that opens with prose - which is how the
    Experiment Log came back summarised as "READY_FOR_REVIEW".
    """
    paragraph = []
    for line in body.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            if paragraph:
                break
            continue
        if text.startswith(("-", "|", "`", ">")):
            if paragraph:
                break
            continue
        paragraph.append(text)
    if not paragraph:
        return ""
    text = " ".join(" ".join(paragraph).split())
    match = _SUMMARY_RE.fullmatch(text)
    if match:
        text = " ".join(match.group(1).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


def _display_path(path):
    """Repo-relative where possible - `capture` accepts a vault anywhere, and
    a note outside the tree must still be quotable rather than fatal."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load_note(path):
    meta, body = _split_frontmatter(path.read_text(errors="replace"))
    return {
        "path": path,
        "rel": _display_path(path),
        "vault_rel": path.relative_to(VAULT).as_posix(),
        "title": meta.get("title") or path.stem,
        "type": meta.get("type", "note"),
        "confidence": meta.get("confidence", ""),
        "tags": meta.get("tags_list", []),
        "summary": _summary_of(body),
        "body": body,
    }


def _field_text(note, field):
    value = note[field]
    return " ".join(value) if isinstance(value, list) else value


def _idf(notes):
    """log-scaled inverse document frequency, so 'knowledge' - a term in most
    of this vault - cannot outweigh 'luminance'."""
    total = max(len(notes), 1)
    seen = collections.Counter()
    for note in notes:
        terms = set()
        for field, _ in _FIELD_WEIGHTS:
            terms.update(_tokens(_field_text(note, field)))
        seen.update(terms)
    return {t: math.log(1 + total / (1 + n)) for t, n in seen.items()}


def score_note(note, query_terms, idf):
    score = 0.0
    for field, weight in _FIELD_WEIGHTS:
        counts = collections.Counter(_tokens(_field_text(note, field)))
        for term in query_terms:
            hits = counts.get(term, 0)
            if hits:
                score += weight * idf.get(term, 1.0) * min(hits, _MAX_FIELD_HITS)
    return score


def _load_graph(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def related_note_files(seed_vault_rels, limit=4):
    """Notes one hop from the seeds in the derived knowledge graph.

    This is what the graph is for: surfacing a note the task's own wording
    would never have matched. It stays a lens - every result is still a note
    path, and nothing here is authoritative over the vault.
    """
    data = _load_graph(GRAPH_JSON)
    if not data:
        return []
    source_of = {n.get("id"): (n.get("source_file") or "") for n in data.get("nodes", [])}
    seeds = {i for i, f in source_of.items() if f in seed_vault_rels}
    if not seeds:
        return []
    weights = collections.Counter()
    for link in data.get("links", []):
        a, b = link.get("source"), link.get("target")
        for near, far in ((a, b), (b, a)):
            if near in seeds:
                target = source_of.get(far, "")
                if target and target not in seed_vault_rels:
                    weights[target] += link.get("weight") or 1
    return [f for f, _ in weights.most_common(limit)]


def code_hits(query_terms, limit=6):
    """Where in the source the task probably lands, as file:line pointers.

    The code graph is 800+ nodes and its Obsidian export is 800+ notes; the
    whole point is to return a handful of coordinates instead of either.
    """
    data = _load_graph(CODE_GRAPH_JSON)
    if not data:
        return []
    scored = []
    for node in data.get("nodes", []):
        label = collections.Counter(_tokens(node.get("label", "")))
        community = collections.Counter(_tokens(node.get("community_name", "")))
        score = sum(2 * min(label.get(t, 0), 2) + min(community.get(t, 0), 1)
                    for t in query_terms)
        if score:
            scored.append((score, node.get("label", "?"), node.get("source_file", ""),
                           node.get("source_location") or "", node.get("community_name", "")))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return scored[:limit]


def state_position():
    """The active position from PAUL's STATE.md.

    Read-only, deliberately: `.paul/` belongs to PAUL, which rewrites STATE.md
    through its own commands. We consume it and never write it.
    """
    if not STATE_MD.exists():
        return []
    lines, inside = [], False
    for line in STATE_MD.read_text(errors="replace").splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith("## Current Position")
            continue
        if inside:
            text = line.strip()
            if text and not text.startswith(("-", "`", "|", "Progress")):
                lines.append(text)
    return lines[:8]


def _scalar(value, key):
    """metadata.json nests both status and experiment. Quote the one field
    that identifies the run; the file stays the place to read the rest."""
    if isinstance(value, dict):
        value = value.get(key)
    return str(value) if value else "-"


def active_projects(limit=4):
    """Most recently touched projects, reduced to id/status/experiment.

    metadata.json stays authoritative for run state - this quotes three
    fields from it, it does not restate it.
    """
    rows = []
    for meta in PROJECTS_DIR.glob("*/metadata.json"):
        try:
            data = json.loads(meta.read_text())
        except (OSError, ValueError):
            continue
        rows.append((meta.stat().st_mtime, meta.parent.name,
                     _scalar(data.get("status"), "overall"),
                     _scalar(data.get("experiment"), "concept_id")))
    rows.sort(reverse=True)
    return [row[1:] for row in rows[:limit]]


def build_context(task, budget=1200, full=0, notes=8, code=6):
    """Render a ranked, budget-capped brief for `task`."""
    query_terms = set(_tokens(task))
    corpus = []
    for path in corpus_notes():
        try:
            corpus.append(load_note(path))
        except OSError:
            continue

    idf = _idf(corpus)
    ranked = sorted(
        ((score_note(n, query_terms, idf), n) for n in corpus),
        key=lambda pair: (-pair[0], pair[1]["title"]),
    )
    hits = [(s, n) for s, n in ranked if s > 0][:notes]

    seeds = {n["vault_rel"] for _, n in hits[:3]}
    by_vault_rel = {n["vault_rel"]: n for n in corpus}
    chosen = {n["vault_rel"] for _, n in hits}
    for vault_rel in related_note_files(seeds):
        if vault_rel in chosen or len(hits) >= notes:
            continue
        note = by_vault_rel.get(vault_rel)
        if note:
            hits.append((GRAPH_NEIGHBOUR_SCORE, note))
            chosen.add(vault_rel)

    out, spent = [], 0
    cap = max(budget, 100) * CHARS_PER_TOKEN - FOOTER_RESERVE_CHARS
    dropped = []

    def emit(line, section=None):
        nonlocal spent
        if spent + len(line) + 1 > cap:
            if section and section not in dropped:
                dropped.append(section)
            return False
        out.append(line)
        spent += len(line) + 1
        return True

    emit(f"# Context for: {task}")
    emit("")

    position = state_position()
    if position:
        emit("## Active state  (.paul/STATE.md)")
        for line in position:
            emit(line, "state")
        emit("")

    projects = active_projects()
    if projects:
        emit("## Recent projects  (projects/<id>/metadata.json is authoritative)")
        for pid, status, experiment in projects:
            emit(f"- {pid}  status={status}  experiment={experiment}", "projects")
        emit("")

    if hits:
        emit(f"## Relevant knowledge  ({len(hits)} of {len(corpus)} notes)")
        for score, note in hits:
            marker = "~" if score == GRAPH_NEIGHBOUR_SCORE else f"{score:.1f}"
            head = f"- [{marker}] {note['title']}  ({note['type']}"
            if note["confidence"]:
                head += f"/{note['confidence']}"
            head += f")  {note['rel']}"
            entry = [head]
            if note["summary"]:
                entry.append(f"      {note['summary']}")
            if spent + sum(len(line) + 1 for line in entry) > cap:
                dropped.append("notes") if "notes" not in dropped else None
                break
            for line in entry:
                emit(line, "notes")
        emit("")

    if full:
        emit(f"## Full text  (top {full})")
        for _, note in hits[:full]:
            emit(f"### {note['rel']}", "full")
            for line in note["body"].strip().splitlines():
                emit(line, "full")
            emit("")

    pointers = code_hits(query_terms, code)
    if pointers:
        emit("## Code pointers  (code graph; open only what you need)")
        for _, label, source_file, location, community in pointers:
            where = f"{source_file}:{location}" if location else source_file
            emit(f"- {label}  {where}  [{community}]", "code")
        emit("")

    if dropped:
        out.append(f"[budget {budget} tokens reached - truncated: {', '.join(dropped)};"
                   " raise --budget or narrow the task]")
    out.append("After the work lands, persist anything durable with "
               "`./content-machine knowledge capture --type ... --evidence ...`.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _require_graphify():
    exe = shutil.which("graphify")
    if not exe:
        raise KnowledgeError(
            "graphify is not on PATH. Install it with `pipx install graphifyy`."
        )
    return exe


def _run(argv):
    log.info("$ %s", " ".join(argv))
    return subprocess.call(argv, cwd=str(ROOT))


def cmd_capture(args):
    body = args.body
    if body is None or body == "-":
        body = sys.stdin.read()
    try:
        path = capture(
            note_type=args.type, title=args.title, summary=args.summary, body=body,
            evidence=args.evidence, links=args.link, tags=args.tag,
            confidence=args.confidence, source_url=args.source_url,
            author=args.author, contributor=args.agent, force=args.force,
        )
    except KnowledgeError as exc:
        log.error("refused: %s", exc)
        return 1
    log.info("captured %s", path.relative_to(ROOT))
    log.info("run `./content-machine knowledge refresh` to fold it into the graph")
    return 0


@contextlib.contextmanager
def _refresh_lock():
    """Serialise refreshes.

    Two concurrent refreshes race on the same output directory and the loser
    silently overwrites the winner with a graph built from an older corpus.
    An exclusive lock turns that into a clear refusal.
    """
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    lock = GRAPH_DIR / ".refresh.lock"
    handle = lock.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise KnowledgeError(
                f"another refresh is already running (lock: {lock}). "
                "Wait for it to finish rather than racing it.")
        yield
    finally:
        handle.close()


def cmd_refresh(args):
    try:
        graphify = _require_graphify()
    except KnowledgeError as exc:
        log.error("%s", exc)
        return 1
    notes = corpus_notes()
    if not notes:
        log.error("no notes in %s - nothing to graph", VAULT)
        return 1
    log.info("corpus: %d notes under %s", len(notes), VAULT.relative_to(ROOT))

    try:
        with _refresh_lock():
            return _refresh(graphify, args)
    except KnowledgeError as exc:
        log.error("%s", exc)
        return 1


def _refresh(graphify, args):
    extract = [graphify, "extract", str(VAULT), "--backend", args.backend]
    if args.force:
        extract.append("--force")
    rc = _run(extract)
    if rc != 0:
        log.error("extraction failed (exit %d); the previous graph is untouched", rc)
        return rc

    # Clustering reuses saved community labels when it finds them, which goes
    # stale the moment the corpus changes shape - it warns and renames
    # communities after their hub instead. Dropping the labels makes it name
    # them with the backend again, so graph.json, graph.html and
    # GRAPH_REPORT.md all come out of one pass agreeing with each other.
    for stale in GRAPH_DIR.glob(".graphify_labels.json*"):
        stale.unlink()

    # Clustering is what produces GRAPH_REPORT.md and graph.html, and names
    # the communities. Extraction alone only writes graph.json.
    rc = _run([graphify, "cluster-only", str(VAULT), "--backend", args.backend])
    if rc != 0:
        log.error("clustering failed (exit %d)", rc)
        return rc

    missing = [p.name for p in (GRAPH_JSON, GRAPH_HTML, GRAPH_REPORT) if not p.exists()]
    if missing:
        log.error("refresh finished but did not produce: %s", ", ".join(missing))
        return 1
    return cmd_status(args)


def cmd_status(args):
    notes = corpus_notes()
    print(f"vault          {VAULT}")
    print(f"notes          {len(notes)}")
    if not GRAPH_JSON.exists():
        print("graph          NOT BUILT - run `./content-machine knowledge refresh`")
        return 1
    nodes, edges, communities = graph_summary()
    built = datetime.fromtimestamp(GRAPH_JSON.stat().st_mtime, timezone.utc)
    print(f"graph          {nodes} nodes, {edges} edges, {communities} communities")
    print(f"built          {built.isoformat(timespec='seconds')}")
    print(f"state          {'STALE - notes changed since the last refresh' if is_stale() else 'CURRENT'}")
    for label, path in (("graph.json", GRAPH_JSON), ("graph.html", GRAPH_HTML),
                        ("GRAPH_REPORT", GRAPH_REPORT)):
        print(f"{label:<14} {path if path.exists() else 'MISSING'}")
    return 0


def cmd_query(args):
    try:
        graphify = _require_graphify()
    except KnowledgeError as exc:
        log.error("%s", exc)
        return 1
    if not GRAPH_JSON.exists():
        log.error("no knowledge graph yet - run `./content-machine knowledge refresh`")
        return 1
    return _run([graphify, "query", args.question, "--graph", str(GRAPH_JSON),
                 "--budget", str(args.budget)])


def cmd_context(args):
    """Retrieve, rank and print - never mutate. Post-work updates go through
    `capture` for knowledge and PAUL's own commands for STATE.md."""
    print(build_context(args.task, budget=args.budget, full=args.full,
                        notes=args.notes, code=args.code))
    if is_stale():
        log.warning("knowledge graph is STALE - `./content-machine knowledge refresh` "
                    "to fold recent notes into the graph expansion")
    return 0


def cmd_open(args):
    if not GRAPH_HTML.exists():
        log.error("no graph.html yet - run `./content-machine knowledge refresh`")
        return 1
    print(GRAPH_HTML)
    opener = shutil.which("xdg-open") or shutil.which("open")
    if opener and os.environ.get("DISPLAY"):
        subprocess.call([opener, str(GRAPH_HTML)])
    else:
        print("No desktop session here. Open the path above in a browser, or "
              "click the link in knowledge/Knowledge Graph.md inside Obsidian.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Obsidian knowledge vault and its derived Graphify graph.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_cap = sub.add_parser(
        "capture", help="persist a knowledge artefact into the Obsidian vault")
    p_cap.add_argument("--type", required=True, choices=sorted(NOTE_TYPES),
                       help="; ".join(f"{k}: {v}" for k, v in sorted(NOTE_TYPES.items())))
    p_cap.add_argument("--title", required=True, help="also the note filename and wikilink target")
    p_cap.add_argument("--summary", required=True, help="one line: what this artefact asserts")
    p_cap.add_argument("--body", default=None,
                       help="note body in markdown; '-' or omitted reads stdin")
    p_cap.add_argument("--evidence", required=True,
                       help="the file, run, measurement or URL this rests on")
    p_cap.add_argument("--link", action="append", default=[],
                       help="wikilink target, repeatable (e.g. --link 'Adult Sleep')")
    p_cap.add_argument("--tag", action="append", default=[], help="extra tag, repeatable")
    p_cap.add_argument("--confidence", default="UNVERIFIED", choices=CONFIDENCE)
    p_cap.add_argument("--source-url", dest="source_url", default=None)
    p_cap.add_argument("--author", default=None, help="who authored the underlying source")
    p_cap.add_argument("--agent", default="content-machine-agent",
                       help="who captured it (recorded as the Graphify contributor)")
    p_cap.add_argument("--force", action="store_true", help="replace an existing note")
    p_cap.set_defaults(func=cmd_capture)

    p_ref = sub.add_parser("refresh", help="rebuild the graph from the current vault")
    p_ref.add_argument("--backend", default=os.environ.get("GRAPHIFY_BACKEND", DEFAULT_BACKEND),
                       help=f"Graphify LLM backend (default: {DEFAULT_BACKEND}, "
                            "which uses the local Claude Code session and costs nothing)")
    p_ref.add_argument("--force", action="store_true",
                       help="re-extract every note, ignoring the incremental cache")
    p_ref.set_defaults(func=cmd_refresh)

    p_stat = sub.add_parser("status", help="corpus size, graph size, and staleness")
    p_stat.set_defaults(func=cmd_status)

    p_q = sub.add_parser("query", help="traverse the knowledge graph for a question")
    p_q.add_argument("question")
    p_q.add_argument("--budget", type=int, default=2000)
    p_q.set_defaults(func=cmd_query)

    p_ctx = sub.add_parser(
        "context", help="ranked, budget-capped context brief for a task")
    p_ctx.add_argument("task", help="what you are about to work on")
    p_ctx.add_argument("--budget", type=int, default=1200,
                       help="hard cap on output size in tokens (default 1200)")
    p_ctx.add_argument("--notes", type=int, default=8, help="max notes to rank in")
    p_ctx.add_argument("--code", type=int, default=6, help="max code pointers")
    p_ctx.add_argument("--full", type=int, default=0, metavar="N",
                       help="also print the full body of the top N notes")
    p_ctx.set_defaults(func=cmd_context)

    p_open = sub.add_parser("open", help="print (and try to open) the interactive graph")
    p_open.set_defaults(func=cmd_open)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

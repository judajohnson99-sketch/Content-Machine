#!/usr/bin/env python3
"""Tests for the knowledge layer.

Covers the capture gate (what is allowed to become a note, and what is
refused), the shape of a captured note, and the corpus/staleness accounting
that `refresh` and `status` depend on.

Graphify is never invoked here: these tests exercise our side of the
contract only, exactly as the generation tests exercise routing without
contacting a provider.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import knowledge  # noqa: E402

CLI = ROOT / "content-machine"
VAULT = ROOT / "knowledge"

BODY = (
    "A body long enough to clear the substance floor, describing what was "
    "learned, why it holds, and what would falsify it. Anything shorter is "
    "operational noise rather than durable knowledge."
)


class TestCaptureGate(unittest.TestCase):
    """The gate is fail-closed: an artefact that cannot justify itself is refused."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def capture(self, **overrides):
        kwargs = dict(
            note_type="finding", title="Test Finding",
            summary="A one-line assertion.", body=BODY,
            evidence="projects/exp-b-sleep-brown/metadata.json",
            vault=self.vault,
        )
        kwargs.update(overrides)
        return knowledge.capture(**kwargs)

    def test_accepted_types_are_the_documented_set(self):
        self.assertEqual(
            set(knowledge.NOTE_TYPES),
            {"decision", "finding", "insight", "constraint", "entity", "topic"})

    def test_unknown_type_is_refused(self):
        with self.assertRaises(knowledge.KnowledgeError):
            self.capture(note_type="render_log")

    def test_capture_without_evidence_is_refused(self):
        with self.assertRaises(knowledge.KnowledgeError):
            self.capture(evidence="")

    def test_trivial_body_is_refused(self):
        with self.assertRaises(knowledge.KnowledgeError):
            self.capture(body="rendered ok")

    def test_missing_title_or_summary_is_refused(self):
        with self.assertRaises(knowledge.KnowledgeError):
            self.capture(title="   ")
        with self.assertRaises(knowledge.KnowledgeError):
            self.capture(summary="")

    def test_unknown_confidence_is_refused(self):
        with self.assertRaises(knowledge.KnowledgeError):
            self.capture(confidence="PROBABLY")

    def test_existing_note_is_not_clobbered_without_force(self):
        path = self.capture()
        with self.assertRaises(knowledge.KnowledgeError):
            self.capture()
        replaced = self.capture(summary="A revised assertion.", force=True)
        self.assertEqual(path, replaced)
        self.assertIn("A revised assertion.", replaced.read_text())


class TestCapturedNoteShape(unittest.TestCase):
    """A captured note must be readable in Obsidian and consumable by Graphify."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self.path = knowledge.capture(
            note_type="insight", title="Brown Noise Clears The Gate",
            summary="Synthesised beds reach review without a licensing question.",
            body=BODY, evidence="projects/exp-b-sleep-brown/metadata.json",
            links=["Adult Sleep", "Publication Gate"], tags=["knowledge/experiment"],
            confidence="VERIFIED", source_url="https://example.invalid/policy",
            author="YouTube", contributor="test-agent", vault=self.vault)
        self.text = self.path.read_text()

    def tearDown(self):
        self.tmp.cleanup()

    def test_note_lands_in_the_captured_folder_named_after_its_title(self):
        self.assertEqual(self.path.parent.name, "Captured")
        self.assertEqual(self.path.name, "Brown Noise Clears The Gate.md")

    def test_frontmatter_uses_graphify_ingest_keys(self):
        head = self.text.split("---")[1]
        for key in ("title:", "type: insight", "confidence: VERIFIED",
                    "captured_at:", "contributor:", "author:", "source_url:",
                    "evidence:"):
            self.assertIn(key, head, f"frontmatter is missing {key!r}")

    def test_links_are_obsidian_wikilinks(self):
        self.assertIn("[[Adult Sleep]]", self.text)
        self.assertIn("[[Publication Gate]]", self.text)

    def test_type_and_custom_tags_are_both_present(self):
        self.assertIn("knowledge/captured", self.text)
        self.assertIn("knowledge/insight", self.text)
        self.assertIn("knowledge/experiment", self.text)

    def test_evidence_survives_into_the_body(self):
        self.assertIn("projects/exp-b-sleep-brown/metadata.json", self.text)


class TestCorpusAccounting(unittest.TestCase):
    """`status` and `refresh` both depend on this accounting being right."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self._saved = (knowledge.VAULT, knowledge.GRAPH_DIR, knowledge.GRAPH_JSON)
        knowledge.VAULT = self.vault
        knowledge.GRAPH_DIR = self.vault / "graphify-out"
        knowledge.GRAPH_JSON = knowledge.GRAPH_DIR / "graph.json"

    def tearDown(self):
        knowledge.VAULT, knowledge.GRAPH_DIR, knowledge.GRAPH_JSON = self._saved
        self.tmp.cleanup()

    def write_graph(self, nodes=2, links=1):
        knowledge.GRAPH_DIR.mkdir(parents=True, exist_ok=True)
        knowledge.GRAPH_JSON.write_text(json.dumps({
            "nodes": [{"id": f"n{i}", "community": i % 2} for i in range(nodes)],
            "links": [{"source": "n0", "target": "n1"} for _ in range(links)],
        }))

    def test_derived_graph_is_excluded_from_its_own_corpus(self):
        (self.vault / "Real Note.md").write_text("# Real Note\n")
        knowledge.GRAPH_DIR.mkdir(parents=True)
        (knowledge.GRAPH_DIR / "GRAPH_REPORT.md").write_text("# Report\n")
        names = [p.name for p in knowledge.corpus_notes()]
        self.assertEqual(names, ["Real Note.md"],
                         "the graph must never be re-ingested as source")

    def test_graph_summary_counts_nodes_edges_and_communities(self):
        self.assertIsNone(knowledge.graph_summary())
        self.write_graph(nodes=4, links=3)
        self.assertEqual(knowledge.graph_summary(), (4, 3, 2))

    def test_missing_graph_is_stale(self):
        (self.vault / "Note.md").write_text("# Note\n")
        self.assertTrue(knowledge.is_stale())

    def test_note_newer_than_graph_is_stale(self):
        note = self.vault / "Note.md"
        note.write_text("# Note\n")
        self.write_graph()
        graph_mtime = knowledge.GRAPH_JSON.stat().st_mtime
        import os
        os.utime(note, (graph_mtime - 10, graph_mtime - 10))
        self.assertFalse(knowledge.is_stale())
        os.utime(note, (graph_mtime + 10, graph_mtime + 10))
        self.assertTrue(knowledge.is_stale())


class TestRefreshLock(unittest.TestCase):
    """Two concurrent refreshes race on one output directory; the loser wins."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = knowledge.GRAPH_DIR
        knowledge.GRAPH_DIR = Path(self.tmp.name) / "graphify-out"

    def tearDown(self):
        knowledge.GRAPH_DIR = self._saved
        self.tmp.cleanup()

    def test_second_refresh_is_refused_while_one_holds_the_lock(self):
        with knowledge._refresh_lock():
            with self.assertRaises(knowledge.KnowledgeError):
                with knowledge._refresh_lock():
                    self.fail("a second refresh acquired the lock")

    def test_lock_is_released_for_the_next_refresh(self):
        with knowledge._refresh_lock():
            pass
        with knowledge._refresh_lock():
            pass


class TestShippedVault(unittest.TestCase):
    """The committed vault is the corpus; these are its structural invariants."""

    def test_entry_point_note_exists_and_links_all_three_artefacts(self):
        note = (VAULT / "Knowledge Graph.md").read_text()
        for artefact in ("graphify-out/graph.html", "graphify-out/GRAPH_REPORT.md",
                         "graphify-out/graph.json"):
            self.assertIn(artefact, note, f"entry point does not link {artefact}")

    def test_vault_holds_source_notes_outside_the_derived_directory(self):
        notes = knowledge.corpus_notes()
        self.assertGreater(len(notes), 5)
        self.assertTrue(all("graphify-out" not in str(p) for p in notes))

    def test_derived_graph_directory_is_gitignored(self):
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "knowledge/graphify-out/graph.json"],
            cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0,
                         "derived graph artefacts must not be committable")

    def test_cli_exposes_the_knowledge_subcommand(self):
        proc = subprocess.run([str(CLI), "knowledge", "--help"],
                              capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for sub in ("capture", "refresh", "status", "query", "open"):
            self.assertIn(sub, proc.stdout)


if __name__ == "__main__":
    unittest.main()

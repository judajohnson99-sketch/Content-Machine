#!/usr/bin/env python3
"""Tests for the context-retrieval layer.

Covers ranking (an on-topic note must outrank an off-topic one), the token
budget (which is what stops the corpus reaching the model), summary
extraction, and graceful degradation when the optional inputs - the graphs
and PAUL's STATE.md - are absent.

Graphify is never invoked: the graphs are read as plain JSON, exactly as the
knowledge tests exercise our side of the contract without shelling out.
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


def write_note(vault, relpath, title, note_type, summary, body, tags=()):
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    front = ["---", f'title: "{title}"', f"type: {note_type}",
             "confidence: VERIFIED", "tags:"]
    front += [f"  - {t}" for t in (tags or ["knowledge/test"])]
    front.append("---")
    path.write_text("\n".join(front) + f"\n\n# {title}\n\n**{summary}**\n\n{body}\n")
    return path


class ContextVaultTest(unittest.TestCase):
    """Ranking and rendering against a controlled vault."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "knowledge"
        self.vault.mkdir()
        self._saved = (knowledge.VAULT, knowledge.GRAPH_DIR, knowledge.GRAPH_JSON,
                       knowledge.CODE_GRAPH_JSON, knowledge.STATE_MD,
                       knowledge.PROJECTS_DIR)
        knowledge.VAULT = self.vault
        knowledge.GRAPH_DIR = self.vault / "graphify-out"
        knowledge.GRAPH_JSON = knowledge.GRAPH_DIR / "graph.json"
        # Point the optional inputs at paths that do not exist, so each test
        # opts in to the ones it is actually about.
        missing = Path(self._tmp.name) / "absent"
        knowledge.CODE_GRAPH_JSON = missing / "graph.json"
        knowledge.STATE_MD = missing / "STATE.md"
        knowledge.PROJECTS_DIR = missing

        write_note(self.vault, "Constraints/Render Throughput.md",
                   "Render Throughput", "constraint",
                   "Encoding an eight-hour ambient render takes about 22 minutes.",
                   "Measured with ffmpeg on this host across three runs.")
        write_note(self.vault, "Niches/Bedtime Stories.md",
                   "Bedtime Stories", "topic",
                   "Narrated long-form sleep stories for an adult audience.",
                   "Depends on local narration being economical.")

    def tearDown(self):
        (knowledge.VAULT, knowledge.GRAPH_DIR, knowledge.GRAPH_JSON,
         knowledge.CODE_GRAPH_JSON, knowledge.STATE_MD,
         knowledge.PROJECTS_DIR) = self._saved
        self._tmp.cleanup()

    def test_on_topic_note_outranks_off_topic(self):
        out = knowledge.build_context("how long does an ambient render encode for")
        self.assertIn("Render Throughput", out)
        self.assertLess(out.index("Render Throughput"),
                        out.index("Bedtime Stories") if "Bedtime Stories" in out else len(out))

    def test_irrelevant_notes_are_excluded_entirely(self):
        out = knowledge.build_context("ffmpeg encoding throughput")
        self.assertIn("Render Throughput", out)
        self.assertNotIn("Bedtime Stories", out)

    def test_summary_line_is_shown_not_the_body(self):
        out = knowledge.build_context("render throughput")
        self.assertIn("about 22 minutes", out)
        self.assertNotIn("across three runs", out)

    def test_budget_is_never_exceeded(self):
        for budget in (100, 200, 400, 1200):
            out = knowledge.build_context("render throughput bedtime stories", budget=budget)
            ceiling = budget * knowledge.CHARS_PER_TOKEN + knowledge.FOOTER_RESERVE_CHARS
            self.assertLessEqual(len(out), ceiling, f"budget {budget} overrun")

    def test_truncation_is_announced(self):
        out = knowledge.build_context("render throughput bedtime stories", budget=100)
        self.assertIn("truncated", out)

    def test_full_prints_bodies_on_request(self):
        out = knowledge.build_context("render throughput", budget=4000, full=1)
        self.assertIn("across three runs", out)

    def test_capture_hint_always_survives_the_budget(self):
        out = knowledge.build_context("render throughput", budget=100)
        self.assertIn("knowledge capture", out)

    def test_graph_neighbour_is_marked_and_ranked_below_lexical_hits(self):
        knowledge.GRAPH_DIR.mkdir(parents=True, exist_ok=True)
        knowledge.GRAPH_JSON.write_text(json.dumps({
            "nodes": [
                {"id": "throughput", "source_file": "Constraints/Render Throughput.md"},
                {"id": "bedtime", "source_file": "Niches/Bedtime Stories.md"},
            ],
            "links": [{"source": "throughput", "target": "bedtime", "weight": 5}],
        }))
        out = knowledge.build_context("ffmpeg encoding throughput")
        self.assertIn("Bedtime Stories", out)
        self.assertIn("[~]", out)
        self.assertLess(out.index("Render Throughput"), out.index("Bedtime Stories"))

    def test_missing_optional_inputs_degrade_quietly(self):
        out = knowledge.build_context("render throughput")
        self.assertNotIn("Active state", out)
        self.assertNotIn("Code pointers", out)
        self.assertIn("Render Throughput", out)


class SummaryExtractionTest(unittest.TestCase):
    """The summary is the first paragraph, not the first bold run."""

    def test_bold_paragraph_is_unwrapped(self):
        self.assertEqual(
            knowledge._summary_of("# T\n\n**The whole claim.**\n\nDetail."),
            "The whole claim.")

    def test_inline_bold_does_not_become_the_summary(self):
        self.assertEqual(
            knowledge._summary_of("# T\n\nA run reached **READY_FOR_REVIEW** today.\n"),
            "A run reached **READY_FOR_REVIEW** today.")

    def test_long_summary_is_capped(self):
        out = knowledge._summary_of("# T\n\n" + ("word " * 200), limit=80)
        self.assertLessEqual(len(out), 80)

    def test_note_without_prose_yields_empty_summary(self):
        self.assertEqual(knowledge._summary_of("# T\n\n- a list item\n"), "")


class StateAndProjectsTest(unittest.TestCase):
    """State is read, never written: .paul/ belongs to PAUL."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self._saved = (knowledge.STATE_MD, knowledge.PROJECTS_DIR)
        knowledge.STATE_MD = self.base / "STATE.md"
        knowledge.PROJECTS_DIR = self.base / "projects"

    def tearDown(self):
        (knowledge.STATE_MD, knowledge.PROJECTS_DIR) = self._saved
        self._tmp.cleanup()

    def test_current_position_is_extracted_and_stops_at_next_heading(self):
        knowledge.STATE_MD.write_text(
            "# Project State\n\n## Current Position\n\nMilestone: v0.1\n"
            "Phase: two\nProgress:\n- Milestone: [==] 20%\n\n"
            "## Loop Position\n\nPLAN\n")
        lines = knowledge.state_position()
        self.assertIn("Milestone: v0.1", lines)
        self.assertIn("Phase: two", lines)
        self.assertNotIn("PLAN", lines)

    def test_absent_state_file_is_not_an_error(self):
        self.assertEqual(knowledge.state_position(), [])

    def test_state_file_is_never_written(self):
        knowledge.STATE_MD.write_text("## Current Position\n\nMilestone: v0.1\n")
        before = knowledge.STATE_MD.read_bytes()
        knowledge.build_context("anything at all")
        self.assertEqual(knowledge.STATE_MD.read_bytes(), before)

    def test_nested_status_and_experiment_are_reduced_to_one_field(self):
        proj = knowledge.PROJECTS_DIR / "exp-1"
        proj.mkdir(parents=True)
        (proj / "metadata.json").write_text(json.dumps({
            "status": {"overall": "READY_FOR_REVIEW", "qc": "PASS"},
            "experiment": {"concept_id": "sleep-brown", "batch_id": "b1"},
        }))
        self.assertEqual(knowledge.active_projects(),
                         [("exp-1", "READY_FOR_REVIEW", "sleep-brown")])

    def test_unreadable_metadata_is_skipped_not_fatal(self):
        proj = knowledge.PROJECTS_DIR / "broken"
        proj.mkdir(parents=True)
        (proj / "metadata.json").write_text("{not json")
        self.assertEqual(knowledge.active_projects(), [])


class ContextCliTest(unittest.TestCase):
    """The command is reachable the way the skill invokes it."""

    def test_cli_returns_a_brief(self):
        proc = subprocess.run(
            [str(CLI), "knowledge", "context", "publication gate provenance",
             "--budget", "500"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("# Context for:", proc.stdout)
        self.assertIn("knowledge capture", proc.stdout)

    def test_cli_output_respects_the_budget(self):
        proc = subprocess.run(
            [str(CLI), "knowledge", "context", "render audio provider gate", "--budget", "300"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ceiling = 300 * knowledge.CHARS_PER_TOKEN + knowledge.FOOTER_RESERVE_CHARS
        self.assertLessEqual(len(proc.stdout), ceiling)


if __name__ == "__main__":
    unittest.main()

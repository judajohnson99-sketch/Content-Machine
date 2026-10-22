import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderWithClient } from "../test/renderWithClient";
import { QualityPanel, formatTimestamp } from "./QualityPanel";
import * as qualityApi from "../api/quality";
import type { QualityEvaluation, QualityReport } from "../types/quality";

function evaluation(overrides: Partial<QualityEvaluation>): QualityEvaluation {
  return {
    id: "scene_intent", title: "Scene imagery vs creative intent", required: true,
    status: "PASS", summary: "nothing measurable is wrong", method: "Measured 12 images.",
    findings: [], measurements: {}, ...overrides,
  };
}

function report(overrides: Partial<QualityReport> = {}): QualityReport {
  return {
    video_id: "vid", generated_utc: "2026-10-22T10:00:00Z", verdict: "REVIEW",
    counts: { block: 0, warn: 1, info: 0 },
    coverage: {
      evaluated: ["scene_intent", "soundtrack_dynamics"],
      unavailable: [{ id: "scene_depiction", title: "Scene depiction (local vision model)",
                      required: false, reason: "no local vision model configured" }],
      not_applicable: [],
    },
    intent: {
      listening_context: { value: "background_sleep", source: "audio manifest" },
      visual_profile: { value: "sleep", label: "sleep / wind-down viewing", source: "listening context" },
      expected_key: { value: "low", evidence: ["moonlit"] },
      palette: { terms: ["deep indigo", "violet"], source: "visual direction", unresolved: [] },
      holds_one_frame: false,
    },
    evaluations: [
      evaluation({}),
      evaluation({ id: "scene_depiction", title: "Scene depiction (local vision model)",
                   required: false, status: "UNAVAILABLE",
                   summary: "no local vision model configured", method: null }),
      evaluation({ id: "soundtrack_dynamics", title: "Soundtrack dynamics", status: "REVIEW",
                   summary: "1 to review",
                   findings: [{ code: "startle_spike", severity: "warn", at_seconds: 3725,
                                detail: "loudness jumps 14 LU above the surrounding bed" }] }),
    ],
    note: "An OK verdict means nothing measurable is wrong.",
    current: true,
    ...overrides,
  };
}

describe("QualityPanel", () => {
  it("shows findings with their position and the intent they were judged against", async () => {
    vi.spyOn(qualityApi, "getQualityReport").mockResolvedValue(report());
    renderWithClient(<QualityPanel videoId="vid" />);

    expect(await screen.findByText("Worth a look")).toBeInTheDocument();
    const dynamics = screen.getByTestId("evaluation-soundtrack_dynamics");
    expect(within(dynamics).getByText("1:02:05")).toBeInTheDocument();
    expect(within(dynamics).getByText(/loudness jumps 14 LU/)).toBeInTheDocument();
    expect(screen.getByText(/sleep \/ wind-down viewing/)).toBeInTheDocument();
  });

  it("never draws an evaluation that did not run as a pass", async () => {
    vi.spyOn(qualityApi, "getQualityReport").mockResolvedValue(report());
    renderWithClient(<QualityPanel videoId="vid" />);

    const depiction = await screen.findByTestId("evaluation-scene_depiction");
    expect(within(depiction).getByText("Not evaluated")).toBeInTheDocument();
    expect(within(depiction).queryByText("Nothing found")).not.toBeInTheDocument();
    expect(screen.getByText(/Not evaluated: Scene depiction/)).toBeInTheDocument();
  });

  it("says when the assessment describes an earlier render", async () => {
    vi.spyOn(qualityApi, "getQualityReport").mockResolvedValue(report({ current: false }));
    renderWithClient(<QualityPanel videoId="vid" />);
    expect(await screen.findByRole("status")).toHaveTextContent(/earlier render/);
  });

  it("an unassessed render is 'not evaluated yet', not a pass", async () => {
    vi.spyOn(qualityApi, "getQualityReport").mockResolvedValue(null);
    renderWithClient(<QualityPanel videoId="vid" />);
    expect(await screen.findByText("Not evaluated yet")).toBeInTheDocument();
  });

  it("formats positions the way the report's own text does", () => {
    expect(formatTimestamp(65)).toBe("1:05");
    expect(formatTimestamp(3725.4)).toBe("1:02:05");
  });
});

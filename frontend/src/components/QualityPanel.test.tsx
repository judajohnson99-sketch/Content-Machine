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
      partial: [],
      unavailable: [{ id: "scene_vision", title: "Scene imagery: depiction, composition, artifacts (local vision model)",
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
      evaluation({ id: "scene_vision", title: "Scene imagery: depiction, composition, artifacts (local vision model)",
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

    const depiction = await screen.findByTestId("evaluation-scene_vision");
    expect(within(depiction).getByText("Not evaluated")).toBeInTheDocument();
    expect(within(depiction).queryByText("Nothing found")).not.toBeInTheDocument();
    expect(within(depiction).queryByTestId("vision-details")).not.toBeInTheDocument();
    expect(screen.getByText(/Not evaluated: Scene imagery/)).toBeInTheDocument();
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

  it("shows the vision dimensions, where the judgements came from, and that the model is uncalibrated", async () => {
    const vision = evaluation({
      id: "scene_vision", title: "Scene imagery: depiction, composition, artifacts (local vision model)",
      required: false, status: "PARTIAL", partial: true,
      summary: "nothing measurable is wrong - partly evaluated: judged 5 of 8 depicted images",
      findings: [{ code: "weak_composition", severity: "info", dimension: "composition", scene_ids: ["s03"],
                   detail: "s03: composition scored 2/5 (qwen3-vl:2b, not yet calibrated) - subject cut off" }],
      measurements: {
        dimensions: {
          adherence: { mean: 4.2, min: 3, flagged: 0 },
          composition: { mean: 3.4, min: 2, flagged: 1 },
          artifacts: { images_with_artifacts: 1, by_name: { blur: 1 } },
          consistency: { assessed: false, reason: "4 judged image(s) is too few" },
        },
        coverage: {
          eligible: 8, judged: 5, abstract_plates_not_judged: 2, sources: { worker: 3, cache: 2 },
          skipped: [{ code: "gpu_busy", detail: "deferred: ComfyUI has 1 running prompt(s)", origin: "this host", count: 3 }],
        },
        models: { "qwen3-vl:2b": { calibrated: ["adherence"], uncalibrated: ["composition", "artifacts", "consistency"] } },
      },
    });
    vi.spyOn(qualityApi, "getQualityReport").mockResolvedValue(report({
      evaluations: [vision],
      coverage: { evaluated: ["scene_vision"], unavailable: [],
                  partial: [{ id: "scene_vision", title: "Scene imagery: depiction, composition, artifacts (local vision model)", reason: "judged 5 of 8" }],
                  not_applicable: [] },
    }));
    renderWithClient(<QualityPanel videoId="vid" />);

    const card = await screen.findByTestId("evaluation-scene_vision");
    expect(within(card).getByText("Partly evaluated")).toBeInTheDocument();
    const details = within(card).getByTestId("vision-details");
    expect(within(details).getByText("Prompt match 4.2/5")).toBeInTheDocument();
    expect(within(details).getByText("Composition 3.4/5")).toBeInTheDocument();
    expect(within(details).getByText("Artifacts in 1 image")).toBeInTheDocument();
    expect(within(details).getByText("Consistency not assessed")).toBeInTheDocument();
    expect(within(details).getByText(/Judged 5 of 8 depicted images \(3 worker, 2 cache\)/)).toBeInTheDocument();
    expect(within(details).getByText(/3 not judged: deferred: ComfyUI has 1 running prompt/)).toBeInTheDocument();
    expect(within(details).getByText(/qwen3-vl:2b \(composition, artifacts, consistency\)/)).toBeInTheDocument();
    expect(within(details).getByText(/has not been calibrated against labelled examples/)).toBeInTheDocument();
    expect(within(card).getByText("composition")).toBeInTheDocument();
    expect(screen.getByTestId("partial-coverage")).toHaveTextContent(/Partly evaluated: Scene imagery/);
  });

  it("formats positions the way the report's own text does", () => {
    expect(formatTimestamp(65)).toBe("1:05");
    expect(formatTimestamp(3725.4)).toBe("1:02:05");
  });
});

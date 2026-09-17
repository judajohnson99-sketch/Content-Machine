import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithClient } from "../test/renderWithClient";
import { PipelineStageGraph } from "./PipelineStageGraph";
import type { LatestRuns, PipelineRun } from "../types/pipeline";

function makeRun(overrides: Partial<PipelineRun>): PipelineRun {
  return {
    id: 1, video_id: "abc", stage: "creative", client_request_id: "x",
    celery_task_id: null, status: "SUCCEEDED", params: {}, exit_code: 0,
    message: "", data: {}, log_tail: "", created_at: "2026-01-01T00:00:00Z",
    started_at: null, finished_at: null,
    ...overrides,
  };
}

const emptyRuns: LatestRuns = {
  research: null, creative: null, storyboard: null, scenes: null,
  audio: null, visuals: null, run: null, produce: null,
};

describe("PipelineStageGraph", () => {
  it("shows NOT_RUN for every stage with no run yet", () => {
    renderWithClient(<PipelineStageGraph videoId="abc" runs={emptyRuns} projectBusy={false} />);
    expect(screen.getAllByText("NOT_RUN").length).toBeGreaterThan(0);
    expect(screen.queryByText("SUCCEEDED")).not.toBeInTheDocument();
  });

  it("reflects a running stage's status and disables its own action", () => {
    const runs = { ...emptyRuns, creative: makeRun({ stage: "creative", status: "RUNNING" }) };
    renderWithClient(<PipelineStageGraph videoId="abc" runs={runs} projectBusy />);
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
  });

  it("surfaces a failed run's message", () => {
    const runs = {
      ...emptyRuns,
      storyboard: makeRun({ stage: "storyboard", status: "FAILED", message: "no script yet" }),
    };
    renderWithClient(<PipelineStageGraph videoId="abc" runs={runs} projectBusy={false} />);
    expect(screen.getByText("no script yet")).toBeInTheDocument();
  });

  it("disables every stage's action while the project is busy", () => {
    renderWithClient(<PipelineStageGraph videoId="abc" runs={emptyRuns} projectBusy />);
    for (const button of screen.getAllByRole("button", { name: "Run" })) {
      expect(button).toBeDisabled();
    }
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "../test/renderWithClient";
import { ReviewPanel } from "./ReviewPanel";
import { ApiError } from "../api/client";
import * as assetsApi from "../api/assets";
import * as projectsApi from "../api/projects";
import * as reviewApi from "../api/review";
import type { ProjectDetail, StatusReport } from "../types/project";

// The embedded DeliverablePanel reads project_assets(); an empty manifest
// keeps these tests about the decision controls, not the preview.
beforeEach(() => {
  vi.spyOn(assetsApi, "getAssets").mockResolvedValue({
    video_id: "abc", video: null, thumbnails: [], images: [], audio: null,
    qc: null, storyboard: null, package: null, logs: [],
  });
});

function project(overrides: Partial<ProjectDetail> = {}): ProjectDetail {
  return {
    video_id: "abc",
    selected_title: "A Video",
    status: { overall: "READY_FOR_REVIEW", gate_digest: "digest-1" },
    ...overrides,
  };
}

function status(overrides: Partial<StatusReport> = {}): StatusReport {
  return {
    video_id: "abc",
    recorded: "READY_FOR_REVIEW",
    verdict: "READY_FOR_REVIEW",
    blocking: [],
    stale: false,
    digest_state: "MATCHES",
    ...overrides,
  };
}

describe("ReviewPanel", () => {
  it("enables both decisions once the gate digest matches and there are no blockers", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project());
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(status());
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);

    renderWithClient(<ReviewPanel videoId="abc" />);

    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
  });

  it("disables approve (but not reject) while blockers remain", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project());
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(
      status({ verdict: "NEEDS_ATTENTION", blocking: ["no title set"] }),
    );
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);

    renderWithClient(<ReviewPanel videoId="abc" />);

    await waitFor(() => expect(screen.getByText("no title set")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
  });

  it("disables both decisions when the recorded gate_digest is stale", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project());
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(status({ digest_state: "CHANGED" }));
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);

    renderWithClient(<ReviewPanel videoId="abc" />);

    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("submits the approval with the project's current gate_digest, never recomputing it", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project({ status: { gate_digest: "the-real-digest" } }));
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(status());
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);
    const record = vi.spyOn(reviewApi, "recordReviewDecision").mockResolvedValue({
      utc: "2026-09-17T00:00:00Z", reviewer: "owner@example.com",
      decision: "approved", notes: "", gate_digest: "the-real-digest",
    });

    renderWithClient(<ReviewPanel videoId="abc" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(record).toHaveBeenCalledWith("abc", "approved", "", "the-real-digest"));
  });

  it("shows a conflict notice, not a hard error, on a 409 refusal", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project());
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(status());
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);
    vi.spyOn(reviewApi, "recordReviewDecision").mockRejectedValue(
      new ApiError(409, "expected_digest is stale"),
    );

    renderWithClient(<ReviewPanel videoId="abc" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/expected_digest is stale/i);
  });

  it("only records a production-grade claim after an explicit confirmation", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project());
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(status());
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue({
      video_id: "abc", video: null, thumbnails: [], audio: null, qc: null, storyboard: null, package: null, logs: [],
      images: [{ path: "images/gen_1.png", bytes: 10, modified_utc: "2026-09-17T00:00:00Z", scene_id: null, generation: null }],
      images_provenance: { provider: "comfyui", model: null, production_grade: null, production_grade_claim: null, notes: null },
      visual_plan: { prompt: "x", negative_prompt: null, style: null },
    });
    const grade = vi.spyOn(reviewApi, "recordVisualGrade").mockResolvedValue({
      utc: "2026-09-17T00:00:00Z", reviewer: "owner@example.com", notes: "", asset_count: 1, production_grade: true,
    });

    renderWithClient(<ReviewPanel videoId="abc" />);
    const mark = await screen.findByRole("button", { name: "Mark production-grade" });
    expect(mark).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/inspected every image/));
    expect(mark).toBeEnabled();
    await userEvent.click(mark);
    await waitFor(() => expect(grade).toHaveBeenCalledWith("abc", true, ""));
  });

  // A synthesised music bed is where the CLI-only audio verdict used to hide:
  // the gate demands it, so the reviewer has to be able to give it here.
  function audioAssets(prov: Record<string, unknown>) {
    return {
      video_id: "abc", video: null, thumbnails: [], images: [], qc: null,
      storyboard: null, package: null, logs: [],
      audio: {
        path: "audio/mix.wav", bytes: 100, modified_utc: "2026-09-19T00:00:00Z",
        seconds: 600, mean_volume_db: -20, layers: [], commercial_use_cleared: true,
        attributions_required: [],
      },
      audio_provenance: {
        kind: "music", source: "music", production_grade_capable: false,
        production_grade: null, graded_by: null, graded_utc: null, grade_notes: null,
        kind_reasoning: "the concept asks for music", chosen_detail: "generative ambient",
        considered: [
          { source: "library", available: false, production_grade_capable: true,
            rights: "declared per track", cost: "free", detail: "no rights-declared track matched" },
          { source: "music", available: true, production_grade_capable: false,
            rights: "generated", cost: "free", detail: "generative ambient" },
        ],
        ...prov,
      },
    };
  }

  it("asks for an audio verdict only after the reviewer confirms they listened", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project());
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(
      status({ verdict: "NEEDS_ATTENTION", blocking: ["audio has no human grade"] }),
    );
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue(audioAssets({}) as any);
    const grade = vi.spyOn(reviewApi, "recordAudioGrade").mockResolvedValue({
      utc: "2026-09-19T00:00:00Z", reviewer: "owner@example.com", notes: "", production_grade: true,
    });

    renderWithClient(<ReviewPanel videoId="abc" />);
    const mark = await screen.findByRole("button", { name: "Audio is publishable" });
    expect(mark).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/listened to this track/));
    expect(mark).toBeEnabled();
    await userEvent.click(mark);
    await waitFor(() => expect(grade).toHaveBeenCalledWith("abc", true, ""));
  });

  it("records a rejection without the listened-to confirmation", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project());
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(status());
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue(audioAssets({}) as any);
    const grade = vi.spyOn(reviewApi, "recordAudioGrade").mockResolvedValue({
      utc: "2026-09-19T00:00:00Z", reviewer: "owner@example.com", notes: "", production_grade: false,
    });

    renderWithClient(<ReviewPanel videoId="abc" />);
    await userEvent.click(await screen.findByRole("button", { name: "Audio is not publishable" }));
    await waitFor(() => expect(grade).toHaveBeenCalledWith("abc", false, ""));
  });

  it("asks for no audio verdict when the source itself is the deliverable", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project());
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(status());
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      audioAssets({ kind: "texture", source: "noise", production_grade_capable: true }) as any,
    );
    const grade = vi.spyOn(reviewApi, "recordAudioGrade");

    renderWithClient(<ReviewPanel videoId="abc" />);
    await waitFor(() => expect(screen.getByText("no verdict required")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Audio is publishable" })).toBeNull();
    expect(grade).not.toHaveBeenCalled();
  });

  it("renders decision history newest first", async () => {
    vi.spyOn(projectsApi, "getProject").mockResolvedValue(project());
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue(status());
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([
      { utc: "2026-09-16T00:00:00Z", reviewer: "alice@example.com", decision: "rejected", notes: "fix title", gate_digest: "d1" },
      { utc: "2026-09-17T00:00:00Z", reviewer: "alice@example.com", decision: "approved", notes: "looks good", gate_digest: "d2" },
    ]);

    renderWithClient(<ReviewPanel videoId="abc" />);

    const items = await screen.findAllByRole("listitem");
    const history = items.filter((li) => li.textContent?.includes("by alice@example.com"));
    expect(history[0]).toHaveTextContent("approved");
    expect(history[1]).toHaveTextContent("rejected");
  });
});

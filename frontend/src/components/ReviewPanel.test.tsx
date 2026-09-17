import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "../test/renderWithClient";
import { ReviewPanel } from "./ReviewPanel";
import { ApiError } from "../api/client";
import * as projectsApi from "../api/projects";
import * as reviewApi from "../api/review";
import type { ProjectDetail, StatusReport } from "../types/project";

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

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { ReviewCenterPage } from "./ReviewCenterPage";
import * as projectsApi from "../../api/projects";
import * as reviewApi from "../../api/review";
import type { ProjectSummary } from "../../types/project";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ReviewCenterPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function summary(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    video_id: "abc", selected_title: "A Video", concept_id: null,
    niche: null, overall_status: "READY_FOR_REVIEW", created_utc: null,
    ...overrides,
  };
}

describe("ReviewCenterPage", () => {
  it("filters the live project list down to reviewable verdicts only", async () => {
    vi.spyOn(projectsApi, "listProjects").mockResolvedValue([
      summary({ video_id: "ready", overall_status: "READY_FOR_REVIEW" }),
      summary({ video_id: "attention", overall_status: "NEEDS_ATTENTION" }),
      summary({ video_id: "draft", overall_status: "DRAFT" }),
      summary({ video_id: "not-rendered", overall_status: "NOT_RENDERED" }),
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("ready")).toBeInTheDocument());
    expect(screen.getByText("attention")).toBeInTheDocument();
    expect(screen.queryByText("draft")).not.toBeInTheDocument();
    expect(screen.queryByText("not-rendered")).not.toBeInTheDocument();
  });

  it("shows an empty state when nothing needs review", async () => {
    vi.spyOn(projectsApi, "listProjects").mockResolvedValue([summary({ overall_status: "DRAFT" })]);

    renderPage();

    expect(await screen.findByText("Nothing awaiting review.")).toBeInTheDocument();
  });

  it("expands a row to mount its ReviewPanel on click", async () => {
    vi.spyOn(projectsApi, "listProjects").mockResolvedValue([summary()]);
    vi.spyOn(projectsApi, "getProject").mockResolvedValue({
      video_id: "abc", selected_title: "A Video",
      status: { overall: "READY_FOR_REVIEW", gate_digest: "d1" },
    });
    vi.spyOn(projectsApi, "getProjectStatus").mockResolvedValue({
      video_id: "abc", recorded: "READY_FOR_REVIEW", verdict: "READY_FOR_REVIEW",
      blocking: [], stale: false, digest_state: "MATCHES",
    });
    vi.spyOn(reviewApi, "listReviewDecisions").mockResolvedValue([]);

    renderPage();
    await waitFor(() => expect(screen.getByRole("button", { name: /A Video/ })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /A Video/ }));

    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
  });
});

import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "../test/renderWithClient";
import { DeliverablePanel } from "./DeliverablePanel";
import { fileUrl } from "../api/assets";
import * as assetsApi from "../api/assets";
import type { ProjectAssets } from "../types/assets";

function assets(overrides: Partial<ProjectAssets> = {}): ProjectAssets {
  return {
    video_id: "abc", video: null, thumbnails: [], images: [], audio: null,
    qc: null, storyboard: null, package: null, logs: [],
    ...overrides,
  };
}

describe("DeliverablePanel", () => {
  it("shows a placeholder, not a broken player, before anything is rendered", async () => {
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue(assets());
    renderWithClient(<DeliverablePanel videoId="abc" />);
    expect(await screen.findByTestId("deliverable-missing")).toBeInTheDocument();
    expect(screen.queryByTestId("deliverable-video")).not.toBeInTheDocument();
    expect(screen.getByText("Not run yet.")).toBeInTheDocument();
  });

  it("plays the render through the files endpoint with the first thumbnail as poster", async () => {
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue(assets({
      video: { path: "output/abc.mp4", bytes: 2048, modified_utc: "2026-09-17T00:00:00Z" },
      thumbnails: [
        { path: "thumbnail/candidate_1.jpg", bytes: 10, modified_utc: "2026-09-17T00:00:00Z" },
        { path: "thumbnail/candidate_2.jpg", bytes: 10, modified_utc: "2026-09-17T00:00:00Z" },
      ],
    }));
    renderWithClient(<DeliverablePanel videoId="abc" />);
    const video = await screen.findByTestId("deliverable-video");
    expect(video).toHaveAttribute("src", fileUrl("abc", "output/abc.mp4"));
    expect(video).toHaveAttribute("poster", fileUrl("abc", "thumbnail/candidate_1.jpg"));
    expect(screen.getAllByRole("img")).toHaveLength(2);
  });

  it("surfaces QC failures and the package's blocking issues verbatim", async () => {
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue(assets({
      qc: {
        status: "FAIL", checks_run: 2, checks_failed: 1, failures: ["black_frames"],
        checks: [
          { check: "file_exists", passed: true, detail: "ok" },
          { check: "black_frames", passed: false, detail: "3.2s of black at 00:10" },
        ],
      },
      package: {
        status: "NEEDS_ATTENTION", generated_utc: "2026-09-17T00:00:00Z",
        path: "output/publication_package.json",
        blocking_issues: ["provenance.images.production_grade is not set"],
      },
    }));
    renderWithClient(<DeliverablePanel videoId="abc" compact />);
    expect(await screen.findByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText(/black_frames/)).toBeInTheDocument();
    expect(screen.getByText("provenance.images.production_grade is not set")).toBeInTheDocument();
    // Compact mode hides passing checks until asked.
    expect(screen.queryByText(/file_exists/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /show all 2 checks/i }));
    expect(screen.getByText(/file_exists/)).toBeInTheDocument();
  });

  it("encodes asset paths safely in file URLs", () => {
    expect(fileUrl("abc", "images/scene 01.png")).toMatch(/\/projects\/abc\/files\/images\/scene%2001\.png$/);
  });
});

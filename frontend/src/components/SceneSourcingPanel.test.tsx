import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "../test/renderWithClient";
import { SceneSourcingPanel } from "./SceneSourcingPanel";
import * as assetsApi from "../api/assets";
import * as visualApi from "../api/visualAssets";
import type { ProjectAssets } from "../types/assets";
import type { SceneDiscovery, VisualCandidate } from "../types/visualAssets";

function candidate(overrides: Partial<VisualCandidate> = {}): VisualCandidate {
  return {
    candidate_id: "stock:pexels:image:501",
    source: "stock",
    provider: "pexels",
    media_kind: "image",
    title: "Quiet lake at night under stars",
    description: "Quiet lake at night under stars",
    tags: ["lake"],
    width: 4000,
    height: 2250,
    duration_seconds: null,
    creator: { name: "Ada Ng", url: null },
    source_url: "https://www.pexels.com/photo/quiet-lake-501/",
    preview_url: "https://images.pexels.com/photos/501/medium.jpg",
    rights: { source: "Pexels", license: "Pexels License", eligibility: "eligible" },
    state: "available",
    ranking: {
      candidate_id: "stock:pexels:image:501", rank: 1, score: 0.82, eligible: true, ready: true,
      recommended: true, confidence: "medium", confidence_reasons: ["pixels not measured"],
      summary: "83% of the scene's key words appear in its description.",
      factors: [{ id: "relevance", label: "Matches the scene", score: 0.83, weight: 0.28, detail: "83% of key words", applied: true }],
      limitations: ["Technical quality was not measured; it needs the file."], advisory: [],
      alternatives: [{ candidate_id: "generated:s02:abc", source: "generated", why: "scores 5 points lower but is not ready (needs the GPU)" }],
    },
    policy: { action: "approval_required", reasons: ["First use of media from Pexels needs your approval."] },
    ...overrides,
  };
}

function generated(): VisualCandidate {
  return candidate({
    candidate_id: "generated:s02:abc", source: "generated", provider: "comfyui", title: "calm lake",
    preview_url: null, state: "deferred", state_detail: "Waiting for the GPU: No GPU worker is enrolled",
    creator: undefined, source_url: null,
    ranking: { ...candidate().ranking, candidate_id: "generated:s02:abc", recommended: false, ready: false, rank: 2, score: 0.77, confidence: "low" },
    policy: { action: "auto", reasons: ["Will be generated when the GPU is available."] },
  });
}

function discovery(overrides: Partial<SceneDiscovery> = {}): SceneDiscovery {
  return {
    scene_id: "s01",
    summary: "Recommended: \"Quiet lake\" from Pexels.",
    recommended: "stock:pexels:image:501",
    candidates: [candidate(), generated()],
    providers: [
      { provider: "pexels", label: "Pexels", state: "ready", detail: "", count: 1 },
      { provider: "pixabay", label: "Pixabay", state: "not_configured", detail: "PIXABAY_API_KEY is not set", count: 0 },
      { provider: "unsplash", label: "Unsplash", state: "rate_limited", detail: "retry in 90s", count: 0 },
    ],
    gpu: { state: "deferred", detail: "No GPU worker is enrolled" },
    ...overrides,
  };
}

const ASSETS = {
  storyboard: { scene_count: 2, timeline_seconds: 8, scenes: [
    { scene_id: "s01", section: "intro", duration_seconds: 4, motion: null, transition: null, image: null, job_id: null },
    { scene_id: "s02", section: "middle", duration_seconds: 4, motion: null, transition: null, image: null, job_id: null },
  ] },
} as unknown as ProjectAssets;

describe("SceneSourcingPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue(ASSETS);
  });

  it("ranks library, stock and a planned generation together with licence and provider states", async () => {
    vi.spyOn(visualApi, "getSceneAssets").mockResolvedValue(discovery());
    renderWithClient(<SceneSourcingPanel videoId="proof" />);

    expect(await screen.findByText("Quiet lake at night under stars")).toBeInTheDocument();
    expect(screen.getByText("Recommended")).toBeInTheDocument();
    expect(screen.getByText("Needs your approval")).toBeInTheDocument();
    expect(screen.getByText("Match 82")).toBeInTheDocument();
    expect(screen.getByText("medium confidence")).toBeInTheDocument();
    expect(screen.getAllByText(/Technical quality was not measured/).length).toBeGreaterThan(0);

    // Unavailable sources are stated plainly; setup details stay out of the way.
    expect(screen.getByText("Pixabay: not connected")).toBeInTheDocument();
    expect(screen.getByText("Unsplash: resting — limit reached")).toBeInTheDocument();
    expect(screen.getByText("New images: waiting for the GPU")).toBeInTheDocument();
    expect(screen.queryByText(/PIXABAY_API_KEY/)).toBeNull();
    expect(screen.getByRole("button", { name: "Generate when the GPU is on" })).toBeEnabled();
  });

  it("asks for approval, shows the terms, and only approves when the owner says so", async () => {
    vi.spyOn(visualApi, "getSceneAssets").mockResolvedValue(discovery({ candidates: [candidate()] }));
    const choose = vi.spyOn(visualApi, "chooseSceneAsset")
      .mockResolvedValueOnce({
        status: "approval_required",
        terms: { source: "Pexels", license: "Pexels License", license_url: "https://www.pexels.com/license/",
                 creator: "Ada Ng", source_url: null, needs_note: false,
                 restrictions: ["Do not sell the file unaltered."], attribution_text: "Photo by Ada Ng on Pexels" },
      })
      .mockResolvedValueOnce({ status: "selected", stale_stages: ["scenes"] });
    renderWithClient(<SceneSourcingPanel videoId="proof" />);

    await userEvent.click(await screen.findByRole("button", { name: "Use for this shot" }));
    expect(choose).toHaveBeenLastCalledWith("proof", "s01", { candidate_id: "stock:pexels:image:501", approve: undefined, note: undefined });
    expect(await screen.findByText("Do not sell the file unaltered.")).toBeInTheDocument();
    expect(screen.getByText(/Photo by Ada Ng on Pexels/)).toBeInTheDocument();

    const approve = screen.getByRole("button", { name: "Approve and use" });
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    await waitFor(() => expect(choose).toHaveBeenLastCalledWith("proof", "s01",
      { candidate_id: "stock:pexels:image:501", approve: true, note: "" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Run the Scenes step again");
  });

  it("requires a note when the rights record is incomplete", async () => {
    vi.spyOn(visualApi, "getSceneAssets").mockResolvedValue(discovery({ candidates: [candidate()] }));
    vi.spyOn(visualApi, "chooseSceneAsset").mockResolvedValue({
      status: "approval_required",
      terms: { source: "Pexels", license: "Pexels License", license_url: null, creator: null, source_url: null,
               needs_note: true, restrictions: [], attribution_text: null },
    });
    renderWithClient(<SceneSourcingPanel videoId="proof" />);
    await userEvent.click(await screen.findByRole("button", { name: "Use for this shot" }));
    await userEvent.click(await screen.findByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Approve and use" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/what did you check/i), "Checked the photo page");
    expect(screen.getByRole("button", { name: "Approve and use" })).toBeEnabled();
  });

  it("uses a library asset without any approval step", async () => {
    const owned = candidate({
      candidate_id: "library:" + "a".repeat(64), source: "library", provider: "library",
      library: { asset_id: "a".repeat(64), usage: [] }, preview_url: null,
      rights: { license: "Owner's own work" },
      policy: { action: "auto", reasons: ["Already in your library with an approved rights record."] },
    });
    vi.spyOn(visualApi, "getSceneAssets").mockResolvedValue(discovery({ candidates: [owned] }));
    const choose = vi.spyOn(visualApi, "chooseSceneAsset").mockResolvedValue({ status: "selected" });
    renderWithClient(<SceneSourcingPanel videoId="proof" />);
    expect(await screen.findByText("Cleared")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use for this shot" }));
    await waitFor(() => expect(choose).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("says a deferred generation was recorded, not failed", async () => {
    vi.spyOn(visualApi, "getSceneAssets").mockResolvedValue(discovery({ candidates: [generated()] }));
    vi.spyOn(visualApi, "chooseSceneAsset").mockResolvedValue({ status: "generation_requested", deferred: true });
    renderWithClient(<SceneSourcingPanel videoId="proof" />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate when the GPU is on" }));
    expect(await screen.findByRole("status")).toHaveTextContent("when the GPU is on");
  });

  it("offers to search stock anyway when the library already has a strong match", async () => {
    vi.spyOn(visualApi, "getSceneAssets").mockResolvedValue(discovery({
      library_hit: true,
      providers: [{ provider: "pexels", label: "Pexels", state: "skipped", detail: "", count: 0 }],
    }));
    const search = vi.spyOn(visualApi, "searchSceneAssets").mockResolvedValue(discovery());
    renderWithClient(<SceneSourcingPanel videoId="proof" />);
    await userEvent.click(await screen.findByRole("button", { name: "Search stock anyway" }));
    await waitFor(() => expect(search).toHaveBeenCalledWith("proof", "s01", { query: undefined, more: true }));
  });

  it("invites a first search when nothing has been searched", async () => {
    vi.spyOn(visualApi, "getSceneAssets").mockResolvedValue({ scene_id: "s01", searched: false, candidates: [] });
    renderWithClient(<SceneSourcingPanel videoId="proof" />);
    await waitFor(() => expect(screen.getByText(/Find options to compare/)).toBeInTheDocument());
  });
});

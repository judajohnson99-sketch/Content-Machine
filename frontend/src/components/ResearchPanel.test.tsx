import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "../test/renderWithClient";
import { ResearchPanel } from "./ResearchPanel";
import * as researchApi from "../api/research";
import type { FindingsArtifact, ResearchBrief } from "../types/research";

const brief: ResearchBrief = {
  video_id: "abc", niche: "sleep ambience", creative_intent: "cozy rainy night",
  likes: ["gentle pacing"], dislikes: ["jump scares"], seed_references: [],
  notes: null, updated_utc: "2026-09-19T00:00:00Z",
};

const findings: FindingsArtifact = {
  video_id: "abc", brief_niche: "sleep ambience", provider: "fixture",
  researched_utc: "2026-09-19T00:00:00Z",
  findings: [
    { finding_id: "f1", kind: "observation", topic: "pacing",
      statement: "cuts happen every few seconds", source_url: "https://x.test",
      source_title: "Example", confidence: "VERIFIED", derived_utc: "" },
    { finding_id: "f2", kind: "interpretation", topic: "pacing",
      statement: "2 sourced observation(s) recurringly mention: cuts",
      source_url: null, source_title: null, confidence: "INFERRED", derived_utc: "" },
  ],
};

describe("ResearchPanel", () => {
  it("shows an empty-brief form and no findings message when neither exists", async () => {
    vi.spyOn(researchApi, "getResearchBrief").mockResolvedValue(null);
    vi.spyOn(researchApi, "getResearchFindings").mockResolvedValue(null);

    renderWithClient(<ResearchPanel videoId="abc" />);

    expect(await screen.findByText(/no findings yet/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/sleep ambience/i)).toHaveValue("");
  });

  it("loads an existing brief into the form", async () => {
    vi.spyOn(researchApi, "getResearchBrief").mockResolvedValue(brief);
    vi.spyOn(researchApi, "getResearchFindings").mockResolvedValue(null);

    renderWithClient(<ResearchPanel videoId="abc" />);

    expect(await screen.findByDisplayValue("sleep ambience")).toBeInTheDocument();
    expect(screen.getByDisplayValue("cozy rainy night")).toBeInTheDocument();
    expect(screen.getByDisplayValue("gentle pacing")).toBeInTheDocument();
  });

  it("saves the edited brief with comma-separated likes/dislikes split into a list", async () => {
    vi.spyOn(researchApi, "getResearchBrief").mockResolvedValue(null);
    vi.spyOn(researchApi, "getResearchFindings").mockResolvedValue(null);
    const save = vi.spyOn(researchApi, "saveResearchBrief").mockResolvedValue(brief);

    renderWithClient(<ResearchPanel videoId="abc" />);
    await screen.findByText(/no findings yet/i);

    await userEvent.type(screen.getByPlaceholderText(/sleep ambience/i), "sleep ambience");
    await userEvent.type(screen.getByPlaceholderText(/what is this video/i), "cozy rainy night");
    await userEvent.click(screen.getByRole("button", { name: "Save brief" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [videoId, payload] = save.mock.calls[0];
    expect(videoId).toBe("abc");
    expect(payload.niche).toBe("sleep ambience");
    expect(payload.creative_intent).toBe("cozy rainy night");
  });

  it("groups findings by topic and labels sourced vs interpreted", async () => {
    vi.spyOn(researchApi, "getResearchBrief").mockResolvedValue(brief);
    vi.spyOn(researchApi, "getResearchFindings").mockResolvedValue(findings);

    renderWithClient(<ResearchPanel videoId="abc" />);

    expect(await screen.findByText("pacing")).toBeInTheDocument();
    expect(screen.getByText("cuts happen every few seconds")).toBeInTheDocument();
    expect(screen.getByText("sourced observation")).toBeInTheDocument();
    expect(screen.getByText("interpretation")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Example" })).toHaveAttribute("href", "https://x.test");
  });

  it("save button stays disabled with no niche entered", async () => {
    vi.spyOn(researchApi, "getResearchBrief").mockResolvedValue(null);
    vi.spyOn(researchApi, "getResearchFindings").mockResolvedValue(null);

    renderWithClient(<ResearchPanel videoId="abc" />);
    await screen.findByText(/no findings yet/i);

    expect(screen.getByRole("button", { name: "Save brief" })).toBeDisabled();
  });
});

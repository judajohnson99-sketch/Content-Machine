import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithClient } from "../test/renderWithClient";
import { AssetsPanel } from "./AssetsPanel";
import * as assetsApi from "../api/assets";
import type { ProjectAssets } from "../types/assets";

function assets(extra: Partial<ProjectAssets>): ProjectAssets {
  return {
    video_id: "proof", video: null, thumbnails: [], images: [], audio: null, qc: null,
    storyboard: null, package: null, logs: [], ...extra,
  } as unknown as ProjectAssets;
}

describe("AssetsPanel picture sources", () => {
  it("shows whose each picture is, on what licence, and who approved stock", async () => {
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue(assets({
      visual_sources: [
        { scene_id: "s01", kind: "library", media_kind: "image", provider: "owner library", license: "Owner's own work" },
        { scene_id: "s02", kind: "stock", media_kind: "image", provider: "Pexels", creator: "Ada Ng",
          license: "Pexels License", source_url: "https://www.pexels.com/photo/quiet-lake-501/",
          approval: { actor: "owner@example.com", utc: "2026-10-22T10:00:00Z" } },
        { scene_id: "s03", kind: "stock", media_kind: "video", provider: "Pexels", creator: "Bo Lind",
          license: "Pexels License", approval: null },
        { scene_id: "s04", kind: "generated", media_kind: "image", provider: "comfyui" },
      ],
      visual_attributions: ["Photo by Ada Ng on Pexels (https://www.pexels.com/photo/quiet-lake-501/)"],
    }));
    renderWithClient(<AssetsPanel videoId="proof" />);

    expect(await screen.findByText("Picture sources")).toBeInTheDocument();
    expect(screen.getByText("Ada Ng")).toBeInTheDocument();
    expect(screen.getByText(/owner@example.com · 2026-10-22/)).toBeInTheDocument();
    expect(screen.getByText("not approved")).toBeInTheDocument();
    expect(screen.getByText("Generated (comfyui)")).toBeInTheDocument();
    expect(screen.getByText(/Credit required in the video description/)).toHaveTextContent("Ada Ng");
  });

  it("shows nothing when there are no recorded sources", async () => {
    vi.spyOn(assetsApi, "getAssets").mockResolvedValue(assets({}));
    renderWithClient(<AssetsPanel videoId="proof" />);
    await screen.findByText(/Storyboard/);
    expect(screen.queryByText("Picture sources")).toBeNull();
  });
});

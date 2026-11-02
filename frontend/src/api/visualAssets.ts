import { apiGet, apiPost } from "./client";
import type { ChoiceResult, SceneDiscovery } from "../types/visualAssets";

const base = (videoId: string, sceneId: string) =>
  `/projects/${encodeURIComponent(videoId)}/scenes/${encodeURIComponent(sceneId)}`;

export function getSceneAssets(videoId: string, sceneId: string): Promise<SceneDiscovery> {
  return apiGet(`${base(videoId, sceneId)}/assets/`);
}

// Searches and ranks; downloads, generates and spends nothing.
export function searchSceneAssets(
  videoId: string,
  sceneId: string,
  body: { query?: string; kind?: "image" | "video" | null; sources?: string[]; more?: boolean } = {},
): Promise<SceneDiscovery> {
  return apiPost(`${base(videoId, sceneId)}/assets/`, body);
}

// `approve` is the owner's explicit first-use approval of external media and
// is never sent unless a person ticked it.
export function chooseSceneAsset(
  videoId: string,
  sceneId: string,
  body: { candidate_id: string; approve?: boolean; note?: string },
): Promise<ChoiceResult> {
  return apiPost(`${base(videoId, sceneId)}/assets/select/`, body);
}

export function saveSceneAsset(
  videoId: string,
  sceneId: string,
  body: { candidate_id: string; approve?: boolean; note?: string },
): Promise<{ asset_id: string; approved: boolean }> {
  return apiPost(`${base(videoId, sceneId)}/assets/save/`, body);
}

export interface FillReport {
  filled: { scene_id: string; asset_id: string }[];
  needs_approval: { scene_id: string; candidate_id: string; provider: string | null; title: string | null }[];
  unfilled: { scene_id: string; reason: string }[];
  skipped: { scene_id: string; reason: string }[];
  stale_stages: string[];
}

// The no-GPU path: pins approved library media on shots with no picture.
// Never approves, downloads or generates anything.
export function fillOpenShots(videoId: string, body: { include_stock?: boolean } = {}): Promise<FillReport> {
  return apiPost(`/projects/${encodeURIComponent(videoId)}/visual-assets/fill/`, body);
}

export function regenerateScene(
  videoId: string,
  sceneId: string,
  body: { seed?: number | null; prompt?: string | null } = {},
): Promise<{ scene_id: string; seed: number; stale_stages: string[] }> {
  return apiPost(`${base(videoId, sceneId)}/regenerate/`, body);
}

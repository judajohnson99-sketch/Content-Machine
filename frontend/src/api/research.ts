import { apiGet, apiPut, ApiError } from "./client";
import type { ResearchBrief, FindingsArtifact, ResearchInfluence } from "../types/research";

// A brief/findings artifact that does not exist yet is a normal state (most
// projects have neither), not an error - callers get null instead of a
// thrown 404, mirroring how listProjectGpuJobs and friends treat "nothing
// yet" as data rather than a failure to render around.
async function getOrNull<T>(path: string): Promise<T | null> {
  try {
    return await apiGet<T>(path);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export function getResearchBrief(videoId: string): Promise<ResearchBrief | null> {
  return getOrNull<ResearchBrief>(`/projects/${videoId}/research/brief/`);
}

export function saveResearchBrief(
  videoId: string, brief: Partial<ResearchBrief>,
): Promise<ResearchBrief> {
  return apiPut<ResearchBrief>(`/projects/${videoId}/research/brief/`, brief);
}

export function getResearchFindings(videoId: string): Promise<FindingsArtifact | null> {
  return getOrNull<FindingsArtifact>(`/projects/${videoId}/research/findings/`);
}

export function getResearchInfluence(videoId: string): Promise<ResearchInfluence | null> {
  return getOrNull<ResearchInfluence>(`/projects/${videoId}/research/influence/`);
}

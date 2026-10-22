import { API_BASE_URL, apiGet, apiPost, apiPut } from "./client";

export interface MediaTransfer { id: string; state: string; message: string; request: { asset_ids: string[]; mode: string } }
export function libraryConnections(): Promise<{ sources: { id: string; name: string; state: string; connected: boolean; detail: string | null }[]; transfers: MediaTransfer[]; visual_analysis_available: boolean; audio_analysis_available: boolean }> {
  return apiGet("/library/connections/");
}
export function retrieveAsset(id: string, mode = "source"): Promise<MediaTransfer> {
  return apiPost(`/library/${encodeURIComponent(id)}/retrieve/`, { mode });
}
export function analyzeAsset(id: string): Promise<unknown> {
  return apiPost(`/library/${encodeURIComponent(id)}/analyze/`, {});
}
export function suggestMedia(project: string, role: OwnerMediaRole): Promise<{ query: string; assets: LibraryAsset[]; reason: string }> {
  return apiGet(`/library/suggestions/?${new URLSearchParams({ project, role })}`);
}
import type {
  LibraryAsset,
  LibraryListing,
  LibraryScanReport,
  OwnerMediaRole,
  OwnerMediaSaveResult,
  OwnerMediaSelection,
  MediaKind,
} from "../types/library";

export function listLibrary(params: { kind?: MediaKind; query?: string } = {}):
  Promise<LibraryListing> {
  const search = new URLSearchParams();
  if (params.kind) search.set("kind", params.kind);
  if (params.query) search.set("query", params.query);
  const suffix = search.toString() ? `?${search}` : "";
  return apiGet<LibraryListing>(`/library/${suffix}`);
}

export function scanLibrary(path: string): Promise<LibraryScanReport> {
  return apiPost<LibraryScanReport>("/library/scan/", { path });
}

// What this asset shows, where it came from and the rights the owner holds.
// These are a person's claims; the API records them and never infers them.
export function annotateAsset(
  id: string,
  body: {
    description: string;
    source: string;
    tags?: string[];
    origin?: string;
    rights?: {
      source: string;
      license: string;
      commercial_use: boolean;
      evidence: string;
      attribution_required?: boolean;
      attribution_text?: string | null;
    } | null;
  },
): Promise<LibraryAsset> {
  return apiPut<LibraryAsset>(`/library/${encodeURIComponent(id)}/`, body);
}

// A browser-fetchable URL for one library asset's own bytes. Resolved by
// scripts.media.resolve() server-side, so a missing, changed or remote
// source answers 404 rather than serving something else.
export function libraryFileUrl(id: string): string {
  return `${API_BASE_URL}/library/${encodeURIComponent(id)}/file/`;
}

export function getOwnerMedia(videoId: string): Promise<OwnerMediaSelection> {
  return apiGet<OwnerMediaSelection>(`/projects/${videoId}/owner-media/`);
}

// Roles left out of `assignments` keep whatever was selected before; a role
// with an empty list hands that stage back to the generators.
export function setOwnerMedia(
  videoId: string,
  assignments: Partial<Record<OwnerMediaRole, string[]>>,
): Promise<OwnerMediaSaveResult> {
  return apiPut<OwnerMediaSaveResult>(`/projects/${videoId}/owner-media/`, assignments);
}

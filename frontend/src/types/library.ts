// Mirrors scripts/ownermedia.py: the owner's catalog as something to choose
// from, and what one production has chosen. The domain read model is the
// contract - nothing here re-derives whether an asset may be used.

export type MediaKind = "image" | "video" | "audio";
export type OwnerMediaRole = "visuals" | "music" | "ambience" | "sfx";

export interface MediaTechnical {
  width: number | null;
  height: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  sample_rate: string | number | null;
  channels: number | null;
  duration_seconds: number | null;
  bytes: number | null;
}

export interface MediaRights {
  source: string | null;
  license: string | null;
  commercial_use: boolean | null;
  attribution_required: boolean;
  attribution_text: string | null;
  evidence: string | null;
  status: string | null;
}

export interface LibraryAsset {
  analysis?: { available: boolean; model: string | null; confidence: string | null; error: string | null };
  id: string;
  kind: MediaKind | null;
  // The file's name, as an identifier only - never as evidence of what
  // it contains.
  filename: string;
  origin: string | null;
  description: string;
  source: string;
  tags: string[];
  technical: MediaTechnical;
  rights: MediaRights | null;
  available: boolean;
  preview_available?: boolean;
  preview_kind?: MediaKind;
  state: "ready" | "unavailable" | "blocked" | "processing";
  resolved_path: string | null;
  // The only verdict a caller needs; `problems` says why when it is false.
  selectable: boolean;
  problems: string[];
  roles: OwnerMediaRole[];
}

export interface LibraryRoleOption {
  role: OwnerMediaRole;
  label: string;
  kinds: MediaKind[];
}

export interface LibraryListing {
  assets: LibraryAsset[];
  roles: LibraryRoleOption[];
}

export interface LibraryScanReport {
  indexed: number;
  issues: { path?: string; problem?: string }[];
}

export interface OwnerMediaEntry {
  asset_id: string;
  role: OwnerMediaRole;
  kind: MediaKind;
  description: string;
  origin: string | null;
  source: string;
  rights: MediaRights | null;
  source_path: string;
  staged_path: string;
  staged_by: string;
  technical: MediaTechnical;
  selected_utc: string | null;
  // Present on the assets read model, not on the selection one.
  file?: { path: string; bytes: number; modified_utc: string } | null;
}

export interface OwnerMediaRoleView {
  label: string;
  count: number;
  entries: OwnerMediaEntry[];
}

export type OwnerVisualsMode = "all" | "mixed";

export interface OwnerMediaSelection {
  updated_utc: string | null;
  actor: string | null;
  // "all": chosen visuals fill every shot (cycled). "mixed": each is placed
  // once, spread evenly, and the remaining shots are generated.
  visuals_mode?: OwnerVisualsMode;
  roles: Record<OwnerMediaRole, OwnerMediaRoleView>;
  // Selected media whose staged bytes are no longer what was chosen. These
  // block review; they are not advisory.
  problems: string[];
  // Stages whose inputs changed since they last ran.
  stale_stages: string[];
}

export interface OwnerMediaSaveResult {
  selection: Omit<OwnerMediaSelection, "problems" | "stale_stages">;
  changed_roles: OwnerMediaRole[];
  stale_stages: string[];
}

export const ROLE_ORDER: OwnerMediaRole[] = ["visuals", "music", "ambience", "sfx"];

export const ROLE_LABEL: Record<OwnerMediaRole, string> = {
  visuals: "Visuals",
  music: "Music",
  ambience: "Ambience",
  sfx: "Sound effects",
};

// What each role replaces when the owner fills it, in the operator's words.
export const ROLE_HINT: Record<OwnerMediaRole, string> = {
  visuals: "Your photos or footage are used for the shots instead of generated imagery.",
  music: "Your track replaces the synthesised music bed.",
  ambience: "Your recording replaces the generated ambience.",
  sfx: "Your effects replace the generated one-shots.",
};

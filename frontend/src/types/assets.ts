// Mirrors scripts.project.project_assets() exactly - the domain read model
// is the contract; the API returns it verbatim. Every `path` is
// project-relative and fetchable through GET /projects/{id}/files/{path}.

export interface AssetFile {
  path: string;
  bytes: number;
  modified_utc: string;
}

export interface ImageAsset extends AssetFile {
  scene_id: string | null;
}

export interface AudioLayer {
  id: string | null;
  provider: string | null;
  license: string | null;
  voice: string | null;
}

export interface AudioAsset extends AssetFile {
  seconds: number | null;
  mean_volume_db: number | null;
  layers: AudioLayer[];
  commercial_use_cleared: boolean | null;
  attributions_required: string[];
}

export interface QcCheck {
  check: string;
  passed: boolean;
  detail: string | null;
}

export interface QcSummary {
  status: string | null;
  checks_run: number | null;
  checks_failed: number | null;
  failures: string[];
  checks: QcCheck[];
}

export interface SceneSummary {
  scene_id: string;
  section: string | null;
  duration_seconds: number | null;
  motion: string | null;
  transition: string | null;
  image: string | null;
  job_id: string | null;
}

export interface StoryboardSummary {
  scene_count: number;
  timeline_seconds: number | null;
  scenes: SceneSummary[];
}

export interface PackageSummary {
  status: string | null;
  blocking_issues: string[];
  generated_utc: string | null;
  path: string;
}

export interface ProjectAssets {
  video_id: string;
  video: AssetFile | null;
  thumbnails: AssetFile[];
  images: ImageAsset[];
  audio: AudioAsset | null;
  qc: QcSummary | null;
  storyboard: StoryboardSummary | null;
  package: PackageSummary | null;
  logs: AssetFile[];
}

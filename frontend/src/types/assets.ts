// Mirrors scripts.project.project_assets() exactly - the domain read model
// is the contract; the API returns it verbatim. Every `path` is
// project-relative and fetchable through GET /projects/{id}/files/{path}.

export interface AssetFile {
  path: string;
  bytes: number;
  modified_utc: string;
}

// How one image was made - scripts.project's _image_lineage(), read from
// the generation job store. Null when the job record is gone.
export interface ImageLineage {
  job_id: string;
  provider: string | null;
  model: string | null;
  worker_id: string | null;
  produces_depicted: boolean | null;
  prompt: string | null;
  negative_prompt: string | null;
  seed: number | null;
  width: number | null;
  height: number | null;
  completed_at: string | null;
  notes: string | null;
}

export interface ImageAsset extends AssetFile {
  scene_id: string | null;
  generation?: ImageLineage | null;
}

export interface ImagesProvenance {
  provider: string | null;
  model: string | null;
  production_grade: boolean | null;
  production_grade_claim: {
    utc: string;
    reviewer: string;
    notes: string;
    asset_count: number;
  } | null;
  notes: string | null;
  // The pictures the storyboard actually uses, and how many files in
  // images/ no scene points at (retries, earlier runs).
  scene_images?: string[];
  unreferenced_count?: number;
}

// Where this project's audio came from and whether that source could be
// production-grade at all - mirrors scripts/project.py::project_assets.
export interface AudioSourceOption {
  source: string;
  available: boolean | null;
  production_grade_capable: boolean | null;
  rights: string | null;
  cost: string | null;
  detail: string | null;
}

export interface AudioProvenance {
  kind: string | null;
  source: string | null;
  production_grade_capable: boolean | null;
  production_grade: boolean | null;
  graded_by: string | null;
  graded_utc: string | null;
  grade_notes: string | null;
  kind_reasoning: string | null;
  chosen_detail: string | null;
  considered: AudioSourceOption[];
}

export interface VisualPlan {
  prompt: string | null;
  negative_prompt: string | null;
  style: string | null;
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

// The editable hand-off: a Kdenlive project, the media it references and a
// portable archive of both. Mirrors project_assets()["editing"].
export interface EditingAssets {
  kdenlive: Record<string, unknown>;
  project: AssetFile | null;
  render: AssetFile | null;
  archive: AssetFile | null;
}

export interface ProjectAssets {
  video_id: string;
  video: AssetFile | null;
  thumbnails: AssetFile[];
  images: ImageAsset[];
  images_provenance?: ImagesProvenance | null;
  visual_plan?: VisualPlan | null;
  audio: AudioAsset | null;
  audio_provenance?: AudioProvenance | null;
  qc: QcSummary | null;
  storyboard: StoryboardSummary | null;
  package: PackageSummary | null;
  editing?: EditingAssets | null;
  logs: AssetFile[];
}

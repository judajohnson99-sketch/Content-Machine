// Mirrors apps/pipeline/serializers.py and apps/pipeline/models.py exactly -
// the API is the source of truth for these shapes and for what a status
// value means; this file only declares it on the TS side.

export type StageName =
  | "research"
  | "creative"
  | "storyboard"
  | "scenes"
  | "audio"
  | "visuals"
  | "run"
  | "produce";

export type RunStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "NEEDS_ATTENTION"
  | "FAILED";

export interface PipelineRun {
  id: number;
  video_id: string;
  stage: StageName;
  client_request_id: string;
  celery_task_id: string | null;
  status: RunStatus;
  params: Record<string, unknown>;
  exit_code: number | null;
  message: string;
  data: Record<string, unknown>;
  log_tail: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

// GET /projects/{id}/pipeline-runs/ - the latest run per stage, keyed by
// stage name; null where that stage has never been triggered from the web.
export type LatestRuns = Record<StageName, PipelineRun | null>;

const ACTIVE: RunStatus[] = ["QUEUED", "RUNNING"];

export function isActive(run: PipelineRun | null | undefined): boolean {
  return !!run && ACTIVE.includes(run.status);
}

// Every stage-trigger body shares client_request_id (architecture plan §6);
// the rest of each shape mirrors its <Stage>RequestSerializer exactly.
export interface StageParams {
  research: { concept_id?: string | null; force?: boolean };
  creative: { force?: boolean };
  storyboard: {
    niche?: string | null;
    scene_count?: number | null;
    source_width?: number | null;
    source_height?: number | null;
    force?: boolean;
  };
  scenes: { force?: boolean; depicted?: boolean };
  audio: { duration?: number | null };
  visuals: {
    prompt?: string | null;
    negative?: string | null;
    count?: number | null;
    width?: number | null;
    height?: number | null;
    seed?: number | null;
    model?: string | null;
    style?: string | null;
    depicted?: boolean;
  };
  run: Record<string, never>;
  produce: {
    concept_id?: string | null;
    duration?: number | null;
    production_grade_visuals?: boolean | null;
  };
}

export interface StageDescriptor {
  stage: StageName;
  label: string;
  description: string;
}

// Display order for the sequential pipeline graph. `produce` is a
// convenience action that chains several of these (see run_produce's own
// docstring in scripts/project.py) rather than a step in the sequence, so
// it is surfaced separately by the Workspace page, not as a ninth node.
export const PIPELINE_STAGES: StageDescriptor[] = [
  { stage: "research", label: "Research", description: "Source-backed subject research (skipped when the concept doesn't need it)." },
  { stage: "creative", label: "Creative Brief", description: "Title, script, description and audio plan." },
  { stage: "storyboard", label: "Storyboard", description: "Scene plan derived from the script and format profile." },
  { stage: "scenes", label: "Scene Images", description: "One generated image per storyboard scene." },
  { stage: "audio", label: "Audio", description: "Compose the project's audio track from its audio plan." },
  { stage: "visuals", label: "Visuals", description: "Generate images via the provider router (non-storyboard projects)." },
  { stage: "run", label: "Assemble & Render", description: "Validate, assemble, QC, package - produces the review verdict." },
];

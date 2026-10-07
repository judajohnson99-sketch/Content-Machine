// Mirrors scripts.experiment.host_capabilities() and scripts.worker's
// depicted_readiness()/job_view() exactly - the domain read models are the
// contract; GET /api/v1/system/readiness/ and .../gpu-jobs/ return them
// verbatim. Nothing here is re-derived client-side.

export type GpuState =
  | "no_worker"
  | "worker_offline"
  | "worker_stale"
  | "comfyui_unavailable"
  | "model_unavailable"
  | "worker_busy"
  | "worker_ready";

export type DepictedState = GpuState | "provider_configured" | "unavailable";

export interface WorkerStatusReport {
  comfyui?: string | null;
  comfyui_reachable?: boolean | null;
  gpu?: string | null;
  vram_total_mb?: number | null;
  vram_free_mb?: number | null;
  checkpoints?: string[];
  model?: string | null;
  workflow?: string | null;
  agent_version?: string | null;
  current_job?: string | null;
}

export interface WorkerView {
  worker_id: string;
  state: "ONLINE" | "STALE" | "OFFLINE";
  capabilities: string[];
  revoked: boolean;
  enrolled_at: string | null;
  last_heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  heartbeats: number;
  status: WorkerStatusReport;
}

export interface QueueSummary {
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  cancelled: number;
}

export interface RemoteGpuReadiness {
  state: GpuState;
  detail: string;
  worker_id: string | null;
  workers: WorkerView[];
  queue: QueueSummary;
}

export interface DepictedImagery {
  state: DepictedState;
  detail: string;
  starts_now: boolean;
  worker_id: string | null;
}

export interface NarrationReadiness {
  available: boolean;
  engine: string;
  voice: string | null;
  detail: string;
}

export interface HostReadiness {
  depicted_image_providers: string[];
  procedural_images: boolean;
  search_provider: string | null;
  search_available: boolean;
  narration_available: boolean;
  narration: NarrationReadiness;
  depicted_imagery: DepictedImagery;
  remote_gpu: RemoteGpuReadiness;
}

export type GpuJobState =
  | "QUEUED"
  | "CLAIMED"
  | "SUBMITTED"
  | "RUNNING"
  | "UPLOADING"
  | "SUCCEEDED"
  | "RETRY_WAIT"
  | "FAILED"
  | "CANCELLED";

export interface GpuJob {
  job_id: string;
  state: GpuJobState;
  wait_reason: string | null;
  attempt: number;
  max_attempts: number | null;
  required_capabilities: string[];
  project_id: string | null;
  label: string | null;
  worker_id: string | null;
  lease_expires_in_seconds: number | null;
  assets: string[];
  error: string | null;
  // "capacity" (the worker's VRAM/memory ceiling) vs "software" (an actual
  // workflow/checkpoint fault) vs null (not FAILED) - so the dashboard can
  // tell "this card is too small for this job" from a real bug.
  failure_category: "capacity" | "software" | null;
  created_at: string | null;
  updated_at: string | null;
  provider_job_id: string | null;
  last_transition: { at: string | null; to: string | null; detail: string | null };
  completed_at: string | null;
}

const GPU_ACTIVE: GpuJobState[] = ["CLAIMED", "SUBMITTED", "RUNNING", "UPLOADING"];
const GPU_WAITING: GpuJobState[] = ["QUEUED", "RETRY_WAIT"];

export function isGpuJobActive(job: GpuJob): boolean {
  return GPU_ACTIVE.includes(job.state);
}

export function isGpuJobWaiting(job: GpuJob): boolean {
  return GPU_WAITING.includes(job.state);
}

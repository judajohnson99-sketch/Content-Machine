// Maps the server-defined status vocabularies (status_report().verdict,
// ProjectSummary.overall_status, PipelineRun.status, worker.py job and
// readiness states) to a handful of semantic tones. Presentation only - it
// never changes what a status *means*; scripts.project / scripts.worker
// stay the one place that decides that.
export type Tone = "success" | "warning" | "danger" | "info" | "neutral" | "accent";

const TONES: Record<string, Tone> = {
  READY_FOR_REVIEW: "success",
  SUCCEEDED: "success",
  APPROVED: "success",
  PASS: "success",
  OK: "success",

  NEEDS_ATTENTION: "warning",
  STALE: "warning",
  RETRY_WAIT: "warning",
  WAITING_FOR_GPU_WORKER: "warning",

  FAILED: "danger",
  FAIL: "danger",
  REJECTED: "danger",
  CANCELLED: "neutral",

  RUNNING: "info",
  QUEUED: "info",
  CLAIMED: "info",
  SUBMITTED: "info",
  UPLOADING: "info",

  NOT_RENDERED: "neutral",
  NOT_RUN: "neutral",
  UNKNOWN: "neutral",
  DRAFT: "neutral",
  MISSING: "neutral",

  ONLINE: "success",
  OFFLINE: "neutral",

  worker_ready: "success",
  provider_configured: "success",
  worker_busy: "info",
  worker_stale: "warning",
  worker_offline: "neutral",
  comfyui_unavailable: "danger",
  model_unavailable: "danger",
  no_worker: "neutral",
  unavailable: "danger",
};

export function statusTone(status: string): Tone {
  return TONES[status] ?? "neutral";
}

// A human-readable label for statuses whose raw form is a loud
// SCREAMING_SNAKE_CASE constant - used only where the raw string itself
// doesn't need to stay verbatim on screen (badges keep the raw string,
// since tests and operators alike rely on it being the literal API value).
export function humanizeStatus(status: string): string {
  return status
    .toLowerCase()
    .split("_")
    .map((w) => w[0]?.toUpperCase() + w.slice(1))
    .join(" ");
}

// Short operator-facing labels for the GPU/depicted-imagery states the
// domain layer reports. The raw state stays available in tooltips.
export const GPU_STATE_LABELS: Record<string, string> = {
  worker_ready: "GPU ready",
  worker_busy: "GPU rendering",
  worker_stale: "GPU stale",
  worker_offline: "GPU offline",
  comfyui_unavailable: "ComfyUI down",
  model_unavailable: "Model missing",
  no_worker: "No GPU worker",
  provider_configured: "Provider ready",
  unavailable: "Unavailable",
};

export function gpuStateLabel(state: string): string {
  return GPU_STATE_LABELS[state] ?? humanizeStatus(state);
}

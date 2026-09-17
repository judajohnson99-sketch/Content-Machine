// Maps the server-defined status vocabularies (status_report().verdict,
// ProjectSummary.overall_status, PipelineRun.status) to one of a handful of
// semantic tones. This is presentation only - it never changes what a
// status *means*; scripts.project stays the one place that decides that.
export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

const TONES: Record<string, Tone> = {
  READY_FOR_REVIEW: "success",
  SUCCEEDED: "success",
  APPROVED: "success",

  NEEDS_ATTENTION: "warning",
  STALE: "warning",

  FAILED: "danger",
  REJECTED: "danger",

  RUNNING: "info",
  QUEUED: "info",

  NOT_RENDERED: "neutral",
  NOT_RUN: "neutral",
  UNKNOWN: "neutral",
  DRAFT: "neutral",
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

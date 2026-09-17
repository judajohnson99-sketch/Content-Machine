// Shared across the project-verdict badges (status_report()'s `verdict`)
// and pipeline-run badges (PipelineRun.status) - both are small closed sets
// of server-defined strings, so one color map/component covers both.
const COLORS: Record<string, string> = {
  READY_FOR_REVIEW: "#1a7f37",
  NEEDS_ATTENTION: "#9a6700",
  NOT_RENDERED: "#57606a",
  UNKNOWN: "#57606a",
  QUEUED: "#57606a",
  RUNNING: "#0969da",
  SUCCEEDED: "#1a7f37",
  FAILED: "#cf222e",
  NOT_RUN: "#8c959f",
};

export function StatusBadge({ status }: { status: string }) {
  const color = COLORS[status] ?? COLORS.UNKNOWN;
  return (
    <span
      style={{
        color: "#fff",
        background: color,
        borderRadius: 4,
        padding: "2px 8px",
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {status}
    </span>
  );
}

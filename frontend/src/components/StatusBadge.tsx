// Shared across the project-verdict badges (status_report()'s `verdict`),
// pipeline-run badges (PipelineRun.status) and worker.py job/worker states -
// all small closed sets of server-defined strings, so one colour map covers
// them. The raw status string is always the visible text - never relabeled -
// since it is the literal value the API and CLI use; the humanised form
// rides along as a tooltip.
import { Badge } from "./ui/Badge";
import { humanizeStatus, statusTone } from "../lib/statusTokens";

const LIVE = new Set(["RUNNING", "QUEUED", "CLAIMED", "SUBMITTED", "UPLOADING"]);

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={statusTone(status)} pulse={LIVE.has(status)} title={humanizeStatus(status)}>
      {status}
    </Badge>
  );
}

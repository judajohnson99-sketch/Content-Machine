// Shared across the project-verdict badges (status_report()'s `verdict`),
// pipeline-run badges (PipelineRun.status) and worker.py job/worker states -
// all small closed sets of server-defined strings, so one colour map covers
// them.
//
// The badge used to print the raw SCREAMING_SNAKE_CASE constant, on the
// reasoning that it is the literal value the API and CLI use. That is true,
// and it is still exactly what `data-status` and the tooltip carry - but it
// made every primary surface read like a log line. The humanised label leads;
// the literal value is one hover (or one DOM attribute) away.
import { Badge } from "./ui/Badge";
import { humanizeStatus, statusTone } from "../lib/statusTokens";

const LIVE = new Set(["RUNNING", "QUEUED", "CLAIMED", "SUBMITTED", "UPLOADING"]);

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={statusTone(status)} pulse={LIVE.has(status)} title={status} data-status={status}>
      {humanizeStatus(status)}
    </Badge>
  );
}

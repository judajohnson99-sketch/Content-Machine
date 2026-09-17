// Shared across the project-verdict badges (status_report()'s `verdict`)
// and pipeline-run badges (PipelineRun.status) - both are small closed sets
// of server-defined strings, so one color map/component covers both. The
// raw status string is always the visible text - never relabeled - since
// it is the literal value the API and CLI use.
import { Badge } from "./ui/Badge";
import { statusTone } from "../lib/statusTokens";

export function StatusBadge({ status }: { status: string }) {
  const pulse = status === "RUNNING" || status === "QUEUED";
  return (
    <Badge tone={statusTone(status)} pulse={pulse}>
      {status}
    </Badge>
  );
}

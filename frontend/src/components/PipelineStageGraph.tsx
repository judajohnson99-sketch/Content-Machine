import { useState } from "react";
import { StageActionButton } from "./StageActionButton";
import { StatusBadge } from "./StatusBadge";
import { PIPELINE_STAGES, isActive } from "../types/pipeline";
import type { LatestRuns, PipelineRun, StageName } from "../types/pipeline";

// Stages whose run_* accepts `force` (scripts/project.py) - the only stage
// parameter this graph exposes, since it is the one control every stage
// list needs ("re-run even though this already succeeded").
const FORCEABLE: StageName[] = ["research", "creative", "storyboard", "scenes"];

interface Props {
  videoId: string;
  runs: LatestRuns;
  projectBusy: boolean;
}

// Renders each stage's *last known state as the backend reported it* -
// nothing here computes whether a stage succeeded, is stale, or is safe to
// re-run; that verdict lives entirely in the PipelineRun row (or, for
// project-wide staleness, status_report()) that WorkspacePage passes down.
export function PipelineStageGraph({ videoId, runs, projectBusy }: Props) {
  return (
    <ol style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 10 }}>
      {PIPELINE_STAGES.map(({ stage, label, description }) => (
        <StageNode
          key={stage}
          videoId={videoId}
          stage={stage}
          label={label}
          description={description}
          run={runs[stage]}
          projectBusy={projectBusy}
        />
      ))}
    </ol>
  );
}

function StageNode({
  videoId, stage, label, description, run, projectBusy,
}: {
  videoId: string;
  stage: StageName;
  label: string;
  description: string;
  run: PipelineRun | null;
  projectBusy: boolean;
}) {
  const [force, setForce] = useState(false);
  const active = isActive(run);
  const disabled = projectBusy || active;
  const disabledReason = active
    ? `${label} is already running.`
    : projectBusy
      ? "Another operation is already in progress for this project."
      : undefined;
  const failed = run && (run.status === "FAILED" || run.status === "NEEDS_ATTENTION");

  return (
    <li
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "10px 14px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        gap: 16,
        flexWrap: "wrap",
      }}
    >
      <div style={{ minWidth: 200 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <strong>{label}</strong>
          <StatusBadge status={run?.status ?? "NOT_RUN"} />
        </div>
        <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--text)" }}>{description}</p>
        {failed && run?.message && (
          <p role="alert" style={{ margin: "4px 0 0", fontSize: 12, color: "#cf222e" }}>
            {run.message}
          </p>
        )}
        {run?.finished_at && (
          <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--text)" }}>
            last finished {new Date(run.finished_at).toLocaleString()}
          </p>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
        {FORCEABLE.includes(stage) && (
          <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <input
              type="checkbox"
              checked={force}
              disabled={disabled}
              onChange={(event) => setForce(event.target.checked)}
            />
            force re-run
          </label>
        )}
        <StageActionButton
          videoId={videoId}
          stage={stage}
          label={active ? "Running…" : "Run"}
          params={FORCEABLE.includes(stage) ? { force } : {}}
          disabled={disabled}
          disabledReason={disabledReason}
        />
      </div>
    </li>
  );
}

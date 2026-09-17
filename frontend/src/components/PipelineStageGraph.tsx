import { useState } from "react";
import { StageActionButton } from "./StageActionButton";
import { StatusBadge } from "./StatusBadge";
import { PIPELINE_STAGES, isActive } from "../types/pipeline";
import type { LatestRuns, PipelineRun, StageName } from "../types/pipeline";
import styles from "./PipelineStageGraph.module.css";

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
    <ol className={styles.list}>
      {PIPELINE_STAGES.map(({ stage, label, description }, i) => (
        <StageNode
          key={stage}
          index={i + 1}
          last={i === PIPELINE_STAGES.length - 1}
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

function markerClass(run: PipelineRun | null, active: boolean): string {
  if (active) return styles.markerInfo;
  if (!run) return "";
  if (run.status === "SUCCEEDED") return styles.markerSuccess;
  if (run.status === "FAILED" || run.status === "NEEDS_ATTENTION") return styles.markerDanger;
  return "";
}

function StageNode({
  index, last, videoId, stage, label, description, run, projectBusy,
}: {
  index: number;
  last: boolean;
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
    <li className={styles.node}>
      <div className={styles.railCol}>
        <div className={`${styles.marker} ${markerClass(run, active)}`}>{index}</div>
        {!last && <div className={styles.rail} />}
      </div>
      <div className={styles.card}>
        <div className={styles.info}>
          <div className={styles.headRow}>
            <span className={styles.label}>{label}</span>
            <StatusBadge status={run?.status ?? "NOT_RUN"} />
          </div>
          <p className={styles.description}>{description}</p>
          {failed && run?.message && <p className={styles.errorMessage} role="alert">{run.message}</p>}
          {run?.finished_at && (
            <p className={styles.finishedAt}>last finished {new Date(run.finished_at).toLocaleString()}</p>
          )}
        </div>
        <div className={styles.controls}>
          {FORCEABLE.includes(stage) && (
            <label className={styles.forceLabel}>
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
      </div>
    </li>
  );
}

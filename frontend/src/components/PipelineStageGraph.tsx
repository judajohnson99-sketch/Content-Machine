import { useState } from "react";
import { StageActionButton } from "./StageActionButton";
import { StatusBadge } from "./StatusBadge";
import { PIPELINE_STAGES, isActive } from "../types/pipeline";
import type { LatestRuns, PipelineRun, StageName } from "../types/pipeline";
import { CheckIcon } from "./ui/icons";
import styles from "./PipelineStageGraph.module.css";

// Stages whose run_* accepts `force` (scripts/project.py) - the only stage
// parameter this graph exposes, since it is the one control every stage
// list needs ("re-run even though this already succeeded").
const FORCEABLE: StageName[] = ["research", "creative", "storyboard", "scenes"];

interface Props {
  videoId: string;
  runs: LatestRuns;
  projectBusy: boolean;
  // metadata.json's own status block, for stages the CLI ran directly (no
  // PipelineRun row exists). Shown as recorded, never re-derived.
  diskStatus?: Record<string, string | null | undefined>;
}

// Which metadata.status key each stage writes (scripts/project.py).
const DISK_KEY: Partial<Record<StageName, string>> = {
  research: "subject_research", storyboard: "storyboard", scenes: "scenes",
  audio: "audio", visuals: "visuals", run: "render",
};

// The visual state of one node. Derived only from the PipelineRun the
// backend reported (its status, and for NEEDS_ATTENTION the
// waiting_for_gpu flag run_visuals/run_scenes set) - never from anything
// re-computed here.
type NodeState = "waiting" | "queued" | "running" | "complete" | "warning" | "failed" | "blocked";

function nodeState(run: PipelineRun | null, disk?: string | null): NodeState {
  if (!run) {
    if (disk === "OK" || disk === "PASS") return "complete";
    if (disk === "FAILED") return "failed";
    if (disk === "WAITING_FOR_GPU_WORKER") return "blocked";
    return "waiting";
  }
  if (run.status === "QUEUED") return "queued";
  if (run.status === "RUNNING") return "running";
  if (run.status === "SUCCEEDED") return "complete";
  if (run.status === "FAILED") return "failed";
  if (run.status === "NEEDS_ATTENTION") return run.data?.waiting_for_gpu ? "blocked" : "warning";
  return "waiting";
}

const STATE_LABEL: Record<NodeState, string> = {
  waiting: "Not run yet",
  queued: "Queued",
  running: "Running",
  complete: "Complete",
  warning: "Needs attention",
  failed: "Failed",
  blocked: "Waiting for GPU worker",
};

// Renders each stage's *last known state as the backend reported it* -
// nothing here computes whether a stage succeeded, is stale, or is safe to
// re-run; that verdict lives entirely in the PipelineRun row (or, for
// project-wide staleness, status_report()) that WorkspacePage passes down.
export function PipelineStageGraph({ videoId, runs, projectBusy, diskStatus }: Props) {
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
          disk={DISK_KEY[stage] ? diskStatus?.[DISK_KEY[stage]!] ?? null : null}
          projectBusy={projectBusy}
        />
      ))}
    </ol>
  );
}

function StageNode({
  index, last, videoId, stage, label, description, run, disk, projectBusy,
}: {
  index: number;
  last: boolean;
  videoId: string;
  stage: StageName;
  label: string;
  description: string;
  run: PipelineRun | null;
  disk: string | null;
  projectBusy: boolean;
}) {
  const [force, setForce] = useState(false);
  const active = isActive(run);
  const state = nodeState(run, disk);
  const disabled = projectBusy || active;
  const disabledReason = active
    ? `${label} is already running.`
    : projectBusy
      ? "Another operation is already in progress for this project."
      : undefined;
  const showMessage = run?.message && (state === "failed" || state === "warning" || state === "blocked");

  return (
    <li className={`${styles.node} ${styles[`state_${state}`]}`} data-state={state}>
      <div className={styles.railCol}>
        <div className={styles.marker} aria-hidden="true">
          {state === "complete" ? <CheckIcon width={14} height={14} /> : index}
        </div>
        {!last && <div className={styles.rail} />}
      </div>
      <div className={styles.card}>
        <div className={styles.info}>
          <div className={styles.headRow}>
            <span className={styles.label}>{label}</span>
            <span className={styles.stateLabel}>{!run && disk ? `On disk: ${STATE_LABEL[state].toLowerCase()}` : STATE_LABEL[state]}</span>
            <StatusBadge status={run?.status ?? (disk || "NOT_RUN")} />
          </div>
          <p className={styles.description}>{description}</p>
          {showMessage && (
            <p className={state === "failed" ? styles.errorMessage : styles.warningMessage} role={state === "failed" ? "alert" : undefined}>
              {run!.message}
            </p>
          )}
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

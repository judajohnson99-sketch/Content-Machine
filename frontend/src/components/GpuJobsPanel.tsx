import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listProjectGpuJobs, requeueGpuJob } from "../api/system";
import { StatusBadge } from "./StatusBadge";
import { ErrorState, InlineSpinner } from "./ui/States";
import { formatRelative } from "../lib/format";
import { isGpuJobActive, isGpuJobWaiting, type GpuJob } from "../types/system";
import styles from "./GpuJobsPanel.module.css";

interface Props {
  videoId: string;
  // Compact: for the workspace inspector - only jobs that are not finished.
  compact?: boolean;
}

const WAIT_LABEL: Record<string, string> = {
  WAITING_FOR_CAPABLE_WORKER: "waiting for the GPU worker to come online",
  READY_TO_CLAIM: "ready - the worker will pick it up on its next poll",
};

// This project's remote GPU jobs, read from scripts.worker's own queue view.
// The one action here is Retry on a finished-badly job, which is the
// operator's own transition (FAILED/CANCELLED -> QUEUED) in scripts.worker;
// everything else only shows where each render is and why it is (or is
// not) moving.
export function GpuJobsPanel({ videoId, compact = false }: Props) {
  const [showAll, setShowAll] = useState(!compact);
  const query = useQuery({
    queryKey: ["gpu-jobs", videoId],
    queryFn: () => listProjectGpuJobs(videoId),
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => isGpuJobActive(j) || isGpuJobWaiting(j)) ? 4_000 : 20_000),
  });

  if (query.isLoading) return <InlineSpinner label="Loading GPU jobs…" />;
  if (query.isError) {
    return (
      <ErrorState
        title="GPU queue unavailable"
        where="GET /api/v1/system/gpu-jobs/"
        hint="Queued renders are unaffected - this is the queue's read view, not the queue itself."
        detail={(query.error as Error).message}
        compact
      />
    );
  }
  const jobs = query.data ?? [];
  if (jobs.length === 0) return null;

  const open = jobs.filter((j) => isGpuJobActive(j) || isGpuJobWaiting(j) || j.state === "FAILED");
  const visible = showAll ? jobs : open;

  return (
    <div className={styles.panel} data-testid="gpu-jobs">
      <ul className={styles.list}>
        {visible.map((job) => (
          <JobRow key={job.job_id} job={job} />
        ))}
      </ul>
      {jobs.length > open.length && (
        <button type="button" className={styles.toggle} onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Hide finished jobs" : `Show all ${jobs.length} jobs`}
        </button>
      )}
    </div>
  );
}

// Exported so a page that submits a job outside this panel's own query (the
// Generate page's manual-render form) can render the same row for it.
export function JobRow({ job }: { job: GpuJob }) {
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: () => requeueGpuJob(job.job_id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gpu-jobs"] }),
  });
  const active = isGpuJobActive(job);
  const retryable = job.state === "FAILED" || job.state === "CANCELLED";
  const waitReason = job.wait_reason?.replace(/ \(.*\)$/, "") ?? null;
  const waitDetail = job.wait_reason?.match(/\((.*)\)/)?.[1];
  return (
    <li className={`${styles.row} ${active ? styles.rowActive : ""}`}>
      <div className={styles.rowHead}>
        <span className={styles.label} title={job.label ?? job.job_id}>{job.label ?? job.job_id}</span>
        <StatusBadge status={job.state} />
      </div>
      <div className={styles.meta}>
        <span className={styles.mono}>{job.job_id.slice(0, 8)}</span>
        {job.worker_id && <span>· {job.worker_id}</span>}
        <span>· attempt {job.attempt}/{job.max_attempts ?? "?"}</span>
        {job.updated_at && <span>· {formatRelative(job.updated_at)}</span>}
      </div>
      {waitReason && (
        <p className={styles.reason}>
          {WAIT_LABEL[waitReason] ?? waitReason.toLowerCase().replace(/_/g, " ")}
          {waitDetail ? ` (${waitDetail})` : ""}
        </p>
      )}
      {active && job.last_transition.detail && <p className={styles.reason}>{job.last_transition.detail}</p>}
      {job.state === "FAILED" && job.error && (
        <p className={styles.error} role="alert">
          {job.failure_category === "capacity" && (
            <span className={styles.capacityTag}>hardware capacity</span>
          )}
          {job.error}
        </p>
      )}
      {retryable && (
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.retry}
            disabled={retry.isPending}
            onClick={() => retry.mutate()}
            aria-label={`Retry ${job.label ?? job.job_id}`}
          >
            {retry.isPending ? "Requeuing…" : "Retry"}
          </button>
          {retry.isError && (
            <span className={styles.error} role="alert">{(retry.error as Error).message}</span>
          )}
        </div>
      )}
    </li>
  );
}

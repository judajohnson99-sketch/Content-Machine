import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { enqueueGpuJob, getReadiness, gpuJobAssetUrl, listGpuJobs } from "../../api/system";
import { ApiError } from "../../api/client";
import { gpuStateLabel, statusTone } from "../../lib/statusTokens";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { PageHeader } from "../../components/ui/PageHeader";
import { JobRow } from "../../components/GpuJobsPanel";
import { isGpuJobActive, isGpuJobWaiting } from "../../types/system";
import styles from "./GeneratePage.module.css";

type Preset = "512x512" | "1920x1080" | "custom";

const PRESETS: Record<Exclude<Preset, "custom">, { width: number; height: number }> = {
  "512x512": { width: 512, height: 512 },
  "1920x1080": { width: 1920, height: 1080 },
};

// A prompt queued here always goes through the same worker queue as every
// other render (scripts.worker.enqueue_manual) - a PC that is offline is
// not an error, the job waits, exactly like the automatic pipeline path.
export function GeneratePage() {
  const readiness = useQuery({ queryKey: ["readiness"], queryFn: getReadiness, refetchInterval: 30_000 });

  const [prompt, setPrompt] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [preset, setPreset] = useState<Preset>("512x512");
  const [width, setWidth] = useState(512);
  const [height, setHeight] = useState(512);
  const [count, setCount] = useState(1);
  const [model, setModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Job ids submitted this session, newest first - the list itself comes
  // from the same GET the GPU-jobs panel uses, polled while any of them is
  // still moving, so a submitted job's live status (including its assets
  // once SUCCEEDED) reflects the real queue rather than a stale snapshot.
  const [submittedIds, setSubmittedIds] = useState<string[]>([]);
  const allJobs = useQuery({
    queryKey: ["gpu-jobs"],
    queryFn: () => listGpuJobs(),
    enabled: submittedIds.length > 0,
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => isGpuJobActive(j) || isGpuJobWaiting(j)) ? 4_000 : 20_000),
  });
  const jobById = new Map((allJobs.data ?? []).map((j) => [j.job_id, j]));
  const jobs = submittedIds.map((id) => jobById.get(id)).filter((j): j is NonNullable<typeof j> => Boolean(j));

  const checkpoints = Array.from(
    new Set(
      (readiness.data?.remote_gpu.workers ?? []).flatMap((w) => w.status.checkpoints ?? []),
    ),
  );

  function choosePreset(p: Preset) {
    setPreset(p);
    if (p !== "custom") {
      setWidth(PRESETS[p].width);
      setHeight(PRESETS[p].height);
    }
  }

  const mutation = useMutation({
    mutationFn: () =>
      enqueueGpuJob({
        prompt: prompt.trim(),
        negative_prompt: negativePrompt.trim() || null,
        width,
        height,
        count,
        model: model || null,
      }),
    onSuccess: (job) => {
      setSubmittedIds((prev) => [job.job_id, ...prev.filter((id) => id !== job.job_id)]);
      setError(null);
    },
    onError: (e: unknown) => {
      setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Request failed.");
    },
  });

  const gpu = readiness.data?.remote_gpu;
  const gpuTone = gpu ? statusTone(gpu.state) : "neutral";
  const promptValid = prompt.trim().length > 0;

  return (
    <div>
      <PageHeader
        backTo={{ to: "/", label: "Dashboard" }}
        eyebrow="GPU worker"
        title="Generate"
        description="Submit an ad-hoc render straight to the remote ComfyUI worker. Queues and waits like any other job if the worker is offline."
      />

      <div className={styles.layout}>
        <Card className={styles.formCard}>
          {gpu && (
            <div className={styles.workerStatus}>
              <Badge tone={gpuTone} title={gpu.detail}>{gpuStateLabel(gpu.state)}</Badge>
              {gpu.state !== "worker_ready" && (
                <span className={styles.hint}>
                  The worker is not ready right now — you can still submit; the job queues and renders when it comes online.
                </span>
              )}
            </div>
          )}

          <form
            className={styles.form}
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              mutation.mutate();
            }}
          >
            <label className={styles.field}>
              <span className={styles.label}>Prompt</span>
              <textarea
                className={styles.textarea}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={3}
                aria-invalid={!promptValid}
              />
            </label>

            <label className={styles.field}>
              <span className={styles.label}>Negative prompt</span>
              <textarea
                className={styles.textarea}
                value={negativePrompt}
                onChange={(e) => setNegativePrompt(e.target.value)}
                rows={2}
              />
            </label>

            <div className={styles.row}>
              <label className={styles.field}>
                <span className={styles.label}>Size</span>
                <select
                  className={styles.select}
                  value={preset}
                  onChange={(e) => choosePreset(e.target.value as Preset)}
                >
                  <option value="512x512">512 × 512</option>
                  <option value="1920x1080">1920 × 1080</option>
                  <option value="custom">Custom</option>
                </select>
              </label>
              {preset === "custom" && (
                <>
                  <label className={styles.field}>
                    <span className={styles.label}>Width</span>
                    <input
                      className={styles.input}
                      type="number"
                      min={64}
                      max={2048}
                      value={width}
                      onChange={(e) => setWidth(Number(e.target.value))}
                    />
                  </label>
                  <label className={styles.field}>
                    <span className={styles.label}>Height</span>
                    <input
                      className={styles.input}
                      type="number"
                      min={64}
                      max={2048}
                      value={height}
                      onChange={(e) => setHeight(Number(e.target.value))}
                    />
                  </label>
                </>
              )}
              <label className={styles.field}>
                <span className={styles.label}>Count</span>
                <input
                  className={styles.input}
                  type="number"
                  min={1}
                  max={4}
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                />
              </label>
            </div>

            {checkpoints.length > 0 && (
              <label className={styles.field}>
                <span className={styles.label}>Model</span>
                <select className={styles.select} value={model} onChange={(e) => setModel(e.target.value)}>
                  <option value="">Worker default</option>
                  {checkpoints.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </label>
            )}

            {error && (
              <p role="alert" className={styles.error}>
                {error}
              </p>
            )}

            <div className={styles.actions}>
              <Button type="submit" variant="primary" disabled={!promptValid || mutation.isPending}>
                {mutation.isPending ? "Queueing…" : "Generate"}
              </Button>
            </div>
          </form>
        </Card>

        <Card className={styles.jobsCard}>
          <h3 className={styles.jobsTitle}>Submitted this session</h3>
          {jobs.length === 0 && <p className={styles.hint}>Nothing submitted yet.</p>}
          <ul className={styles.jobsList}>
            {jobs.map((job) => (
              <li key={job.job_id}>
                <JobRow job={job} />
                {job.state === "SUCCEEDED" && job.assets.length > 0 && (
                  <div className={styles.assets}>
                    {job.assets.map((_, i) => (
                      <img
                        key={i}
                        className={styles.assetImage}
                        src={gpuJobAssetUrl(job.job_id, i)}
                        alt={job.label ?? job.job_id}
                      />
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

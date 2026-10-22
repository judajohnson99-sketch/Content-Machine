import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { MediaDialog } from "../../components/MediaDialog";
import { getOwnerMedia, setOwnerMedia } from "../../api/library";
import { listProjects } from "../../api/projects";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueGpuJob, getReadiness, gpuJobAssetUrl, listGpuJobs, saveGpuJobAsset } from "../../api/system";
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
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const [target, setTarget] = useState(params.get("project") ?? "");
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
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

    refetchInterval: (q) => ((q.state.data ?? []).some((j) => isGpuJobActive(j) || isGpuJobWaiting(j)) ? 4_000 : 20_000),
  });
  const jobById = new Map((allJobs.data ?? []).map((j) => [j.job_id, j]));
  const jobs = [...new Set([...submittedIds, ...(allJobs.data ?? []).filter(j => !j.project_id).map(j => j.job_id)])].map(id => jobById.get(id)).filter((j): j is NonNullable<typeof j> => Boolean(j));
  const focusJobs = submittedIds.length ? jobs.filter(job => submittedIds.includes(job.job_id)) : jobs.slice(0, 1);
  const completedJobs = focusJobs.filter((job) => job.state === "SUCCEEDED" && job.assets.length > 0);
  const [selectedResult, setSelectedResult] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<{ jobId: string; index: number; label: string } | null>(null);
  const [savedResults, setSavedResults] = useState<Set<string>>(new Set());

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
        seed: crypto.getRandomValues(new Uint32Array(1))[0] % 2147483647,
      }),
    onSuccess: (job) => {
      setSubmittedIds((prev) => [job.job_id, ...prev.filter((id) => id !== job.job_id)]);
      setError(null);
    },
    onError: (e: unknown) => {
      setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Request failed.");
    },
  });
  const saveMutation = useMutation({
    mutationFn: ({ jobId, index }: { jobId: string; index: number }) => saveGpuJobAsset(jobId, index),
    onSuccess: (_result, variables) => {
      setSavedResults((current) => new Set(current).add(`${variables.jobId}:${variables.index}`));
      queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  const useResult = useMutation({
    mutationFn: async ({ jobId, index }: { jobId: string; index: number }) => {
      const saved = await saveGpuJobAsset(jobId, index);
      const selection = await getOwnerMedia(target);
      const ids = selection.roles.visuals?.entries.map(entry => entry.asset_id) ?? [];
      await setOwnerMedia(target, { visuals: [...new Set([...ids, saved.id])] });
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["owner-media", target] }); queryClient.invalidateQueries({ queryKey: ["library"] }); },
  });
  const gpu = readiness.data?.remote_gpu;
  const gpuTone = gpu ? statusTone(gpu.state) : "neutral";
  const promptValid = prompt.trim().length > 0;

  return (
    <div>
      <PageHeader
        backTo={{ to: "/", label: "Dashboard" }}
        eyebrow="A space for visual ideas"
        title="Image Lab"
        description="Describe a scene. Explore the results. Make your favourite part of a film."
      />

      <div className={`${styles.layout} ${submittedIds.length && completedJobs.length ? styles.hasResults : ""}`}>
        <Card className={styles.formCard}>
          {gpu && (
            <div className={styles.workerStatus}>
              <Badge tone={gpuTone} title={gpu.detail}>{gpuStateLabel(gpu.state)}</Badge>
              {gpu.state !== "worker_ready" && (
                <span className={styles.hint}>
                  Your image studio is temporarily unavailable. You can submit now; generation starts when your connected computer is ready.
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
                <span className={styles.label}>Final image size</span>
                <select
                  className={styles.select}
                  value={preset}
                  onChange={(e) => choosePreset(e.target.value as Preset)}
                >
                  <option value="512x512">Square · 512 × 512</option>
                  <option value="1920x1080">Widescreen · Full HD</option>
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
                  <option value="">Automatic</option>
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

        <Card className={styles.resultsCard} aria-live="polite">
          <div className={styles.resultsHead}>
            <div>
              <p className={styles.kicker}>Image Lab</p>
              <h2 className={styles.resultsTitle}>{completedJobs.length ? "Your generated images" : "Results appear here"}</h2>
              <p className={styles.hint}>{completedJobs.length ? "Inspect a result, choose it for your production, or make a variant." : "Generate an image to bring the finished result into focus."}</p>
            </div>
            {jobs.some((job) => isGpuJobActive(job) || isGpuJobWaiting(job)) && <Badge tone="info">Generating…</Badge>}
          </div>
          {completedJobs.length > 0 ? (
            <div className={`${styles.gallery} ${completedJobs.flatMap((job) => job.assets).length === 1 ? styles.gallerySingle : ""}`}>
              {completedJobs.flatMap((job) => job.assets.map((_, index) => ({ job, index }))).map(({ job, index }) => {
                const key = `${job.job_id}:${index}`;
                const selected = selectedResult === key;
                return (
                  <article className={`${styles.resultCard} ${selected ? styles.resultSelected : ""}`} key={key}>
                    <button type="button" className={styles.imageButton} onClick={() => setLightbox({ jobId: job.job_id, index, label: job.label ?? "Generated image" })} aria-label={`View ${job.label ?? "generated image"}`}>
                      <img className={styles.resultImage} src={gpuJobAssetUrl(job.job_id, index)} alt={job.label ?? "Generated image"} />
                    </button>
                    <div className={styles.resultBody}>
                      <strong>{job.label ?? "Generated image"}</strong>
                      <span className={styles.resultMeta}>Result {index + 1} of {job.assets.length}{job.request ? ` · ${job.request.width} × ${job.request.height}` : ""}</span>
                      <div className={styles.resultActions}>
                        <Button size="sm" variant="secondary" onClick={() => setLightbox({ jobId: job.job_id, index, label: job.label ?? "Generated image" })}>View</Button>
                        <Button size="sm" variant={selected ? "success" : "primary"} onClick={() => setSelectedResult(key)}>{selected ? "Selected" : "Choose image"}</Button>
                        <Button size="sm" variant="ghost" disabled={savedResults.has(key) || saveMutation.isPending} onClick={() => saveMutation.mutate({ jobId: job.job_id, index })}>
                          {savedResults.has(key) ? "Saved to Assets" : saveMutation.isPending ? "Saving…" : "Save to Assets"}
                        </Button>
                        <Button size="sm" variant="ghost" disabled={!job.request?.prompt || mutation.isPending} title={!job.request?.prompt ? "Open generation details to recover this older prompt" : "Load this image’s prompt to create a variation"} onClick={() => { setPrompt(job.request?.prompt || ""); setNegativePrompt(job.request?.negative_prompt || ""); setModel(job.request?.model || ""); document.querySelector("textarea")?.focus(); }}>Make a variation</Button>
                      </div>
                      <details className={styles.resultDetails}>
                        <summary>Generation details</summary>
                        <p>Prompt: {job.request?.prompt ?? job.label ?? "Recorded in generation history"}</p>
                        {job.request?.negative_prompt && <p>Negative prompt: {job.request.negative_prompt}</p>}
                        <p>Model: {job.request?.model || "Automatic"} · Seed/settings are preserved in the job record.</p>
                      </details>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : focusJobs.length === 0 ? <p className={styles.emptyResults}>No images yet.</p> : <ul className={styles.progressResults}>{focusJobs.map((job) => <JobRow key={job.job_id} job={job} />)}</ul>}
          {allJobs.isError && <p role="alert">Could not load generation history. Refresh to retry.</p>}
          {selectedResult && <div className={styles.actions}><label className={styles.field}><span className={styles.label}>Use selected image in</span><select className={styles.select} aria-label="Target production" value={target} onChange={e => setTarget(e.target.value)}><option value="">Choose a production</option>{projects.data?.map(p => <option key={p.video_id} value={p.video_id}>{p.selected_title || p.video_id}</option>)}</select></label><Button variant="primary" disabled={!target || useResult.isPending} onClick={() => { const [jobId, index] = selectedResult.split(":"); useResult.mutate({ jobId, index: Number(index) }); }}>Use in production</Button>{!target && <span>Choose a production above.</span>}</div>}
          {useResult.isSuccess && <p role="status">Image added. <Link to={`/projects/${target}?step=Media`}>Open production →</Link></p>}
          {useResult.isError && <p role="alert" className={styles.error}>{useResult.error.message}</p>}
          {saveMutation.isError && <p role="alert" className={styles.error}>Could not save this result: {(saveMutation.error as Error).message}</p>}
        </Card>

        <details className={styles.history}>
          <summary>Session history <span>{jobs.length} submitted</span></summary>
          <ul className={styles.jobsList}>
            {jobs.map((job) => <li key={job.job_id}><Button size="sm" variant="ghost" onClick={() => setSubmittedIds([job.job_id])}>Open results</Button><ul><JobRow job={job} /></ul></li>)}
          </ul>
        </details>
      </div>
      {lightbox && (
        <MediaDialog title="Image detail" onClose={() => setLightbox(null)}>
          <img src={gpuJobAssetUrl(lightbox.jobId, lightbox.index)} alt={lightbox.label} className={styles.lightboxImage} />
        </MediaDialog>
      )}
    </div>
  );
}

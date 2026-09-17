import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getProject, getProjectStatus } from "../../api/projects";
import { listRuns } from "../../api/pipeline";
import { getAssets } from "../../api/assets";
import { listProjectGpuJobs } from "../../api/system";
import { StatusBadge } from "../../components/StatusBadge";
import { StageActionButton } from "../../components/StageActionButton";
import { PipelineStageGraph } from "../../components/PipelineStageGraph";
import { DeliverablePanel } from "../../components/DeliverablePanel";
import { AssetsPanel } from "../../components/AssetsPanel";
import { GpuJobsPanel } from "../../components/GpuJobsPanel";
import { PageHeader } from "../../components/ui/PageHeader";
import { Card, CardHeader } from "../../components/ui/Card";
import { EmptyState, ErrorState, SkeletonRows } from "../../components/ui/States";
import { CheckCircleIcon, FilmIcon, GpuIcon, ImageIcon } from "../../components/ui/icons";
import { isActive, PIPELINE_STAGES } from "../../types/pipeline";
import type { LatestRuns, PipelineRun } from "../../types/pipeline";
import { isGpuJobActive, isGpuJobWaiting } from "../../types/system";
import { isReviewable } from "../../lib/projectStatus";
import { formatDateTime, formatRelative } from "../../lib/format";
import styles from "./WorkspacePage.module.css";

function anyStageActive(runs: LatestRuns | undefined): boolean {
  if (!runs) return false;
  return Object.values(runs).some(isActive);
}

// The run the operator most needs to see: whatever is in flight, else the
// most recently finished one. Purely a choice of which PipelineRun row to
// show - its status/message are the backend's words, not re-derived.
function focusRun(runs: LatestRuns | undefined): PipelineRun | null {
  if (!runs) return null;
  const all = Object.values(runs).filter((r): r is PipelineRun => r !== null);
  const active = all.find(isActive);
  if (active) return active;
  return all.sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0] ?? null;
}

const STAGE_LABEL = Object.fromEntries(PIPELINE_STAGES.map((s) => [s.stage, s.label])) as Record<string, string>;
STAGE_LABEL.produce = "Produce";

export function WorkspacePage() {
  const { videoId } = useParams<{ videoId: string }>();
  const queryClient = useQueryClient();
  // null = automatic (scripts.project.produce_uses_scenes decides);
  // true/false = the operator's explicit override, sent as `scenes`.
  const [scenesOverride, setScenesOverride] = useState<boolean | null>(null);
  const [showLog, setShowLog] = useState(false);

  const projectQuery = useQuery({
    queryKey: ["project", videoId],
    queryFn: () => getProject(videoId!),
    enabled: !!videoId,
  });

  const statusQuery = useQuery({
    queryKey: ["project-status", videoId],
    queryFn: () => getProjectStatus(videoId!),
    enabled: !!videoId,
    refetchInterval: 10_000,
  });

  const runsQuery = useQuery({
    queryKey: ["pipeline-runs", videoId],
    queryFn: () => listRuns(videoId!),
    enabled: !!videoId,
    // Poll fast while something is in flight so the graph feels live;
    // fall back to a slow trickle otherwise (a CLI run on the same
    // project still needs to be reflected eventually).
    refetchInterval: (query) => (anyStageActive(query.state.data) ? 2_500 : 15_000),
  });

  const assetsQuery = useQuery({
    queryKey: ["project-assets", videoId],
    queryFn: () => getAssets(videoId!),
    enabled: !!videoId,
    refetchInterval: 15_000,
  });

  const gpuQuery = useQuery({
    queryKey: ["gpu-jobs", videoId],
    queryFn: () => listProjectGpuJobs(videoId!),
    enabled: !!videoId,
    refetchInterval: 6_000,
  });

  const busy = anyStageActive(runsQuery.data);
  const wasBusy = useRef(false);
  // When a run finishes, whatever it produced (render, assets, verdict,
  // title) is on disk now - refetch immediately rather than waiting for
  // each panel's own poll interval.
  useEffect(() => {
    if (wasBusy.current && !busy && videoId) {
      queryClient.invalidateQueries({ queryKey: ["project-assets", videoId] });
      queryClient.invalidateQueries({ queryKey: ["project", videoId] });
      queryClient.invalidateQueries({ queryKey: ["project-status", videoId] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["gpu-jobs", videoId] });
    }
    wasBusy.current = busy;
  }, [busy, videoId, queryClient]);

  // The same for remote renders: the moment the GPU queue for this project
  // goes quiet, the images (and the resume-ability of Produce) are on disk.
  const gpuOpen = (gpuQuery.data ?? []).filter((j) => isGpuJobActive(j) || isGpuJobWaiting(j)).length;
  const hadGpuOpen = useRef(0);
  useEffect(() => {
    if (hadGpuOpen.current > 0 && gpuOpen === 0 && videoId) {
      queryClient.invalidateQueries({ queryKey: ["project-assets", videoId] });
      queryClient.invalidateQueries({ queryKey: ["project-status", videoId] });
    }
    hadGpuOpen.current = gpuOpen;
  }, [gpuOpen, videoId, queryClient]);

  if (!videoId) return <ErrorState title="No project specified" />;

  const focus = focusRun(runsQuery.data);
  const assets = assetsQuery.data;
  const status = statusQuery.data;
  const project = projectQuery.data;
  const waitingForGpu = !!focus && focus.status === "NEEDS_ATTENTION" && !!focus.data?.waiting_for_gpu;
  const gpuLanded = waitingForGpu && gpuQuery.data !== undefined && gpuOpen === 0;
  const gpuFailed = (gpuQuery.data ?? []).filter((j) => j.state === "FAILED");
  const currentStage = focus ? STAGE_LABEL[focus.stage] ?? focus.stage : null;

  return (
    <div className={styles.page}>
      <PageHeader
        backTo={{ to: "/", label: "Dashboard" }}
        eyebrow={project?.experiment?.niche ? project.experiment.niche.replace(/_/g, " ") : "Production"}
        title={project ? project.selected_title || project.video_id : videoId}
        description={
          <span className={styles.identity}>
            <code>{videoId}</code>
            {project?.experiment?.concept_id && <span> · concept <code>{project.experiment.concept_id}</code></span>}
            {status && (
              <span className={styles.identityStatus}>
                <StatusBadge status={status.verdict} />
                {status.stale && <span className={styles.stale}>stale — recorded as {status.recorded}</span>}
              </span>
            )}
          </span>
        }
        actions={
          status && isReviewable(status.verdict) ? (
            <Link to={`/review?project=${videoId}`} className={styles.reviewLink}>
              <CheckCircleIcon width={16} height={16} /> Open in Review Center
            </Link>
          ) : undefined
        }
      />

      {projectQuery.isError && (
        <ErrorState
          title="Failed to load project"
          where={`GET /api/v1/projects/${videoId}/`}
          description={(projectQuery.error as Error).message}
          hint="A 404 means no such project directory exists under projects/. Check the id in the URL or create the production from the catalogue."
          action={<Link to="/projects/new" className={styles.reviewLink}>Start a new production</Link>}
        />
      )}

      {projectQuery.isLoading ? (
        <SkeletonRows count={5} />
      ) : projectQuery.isError ? null : (
        <div className={styles.workstation}>
          {/* ---- Main creative canvas ---- */}
          <div className={styles.canvasCol}>
            <Card padded={false} className={styles.canvasCard}>
              <div className={styles.canvasHead}>
                <CardHeader
                  eyebrow="Deliverable"
                  title={assets?.video ? "Current render" : assets?.images.length ? "Generated imagery" : "Nothing rendered yet"}
                  description={
                    assets?.video
                      ? `${assets.video.path} · rendered ${formatDateTime(assets.video.modified_utc)}`
                      : assets?.images.length
                        ? `${assets.images.length} image${assets.images.length === 1 ? "" : "s"} on disk; the render follows Assemble & Render.`
                        : "Run Produce to generate imagery and audio, then assemble and render the deliverable."
                  }
                />
              </div>
              <div className={styles.canvas}>
                {assetsQuery.isLoading && <SkeletonRows count={3} />}
                {assets && (assets.video || assets.images.length > 0 ? (
                  <DeliverablePanel videoId={videoId} />
                ) : (
                  <EmptyState
                    icon={<FilmIcon width={24} height={24} />}
                    title="No media yet"
                    description="Imagery, audio and the render appear here as the pipeline produces them. Start with Produce."
                  />
                ))}
              </div>
            </Card>

            <Card>
              <CardHeader
                eyebrow="Pipeline"
                title={currentStage ? `Current stage: ${currentStage}` : "Pipeline"}
                description="Each stage's last web run, or what metadata.json records for it when the CLI ran it. Re-run any stage; force re-runs what already succeeded."
              />
              {runsQuery.isLoading && <SkeletonRows count={4} />}
              {runsQuery.isError && (
                <ErrorState
                  compact
                  title="Failed to load pipeline state"
                  where={`GET /api/v1/projects/${videoId}/pipeline-runs/`}
                  description={(runsQuery.error as Error).message}
                />
              )}
              {runsQuery.data && (
                <PipelineStageGraph videoId={videoId} runs={runsQuery.data} projectBusy={busy} diskStatus={project?.status ?? undefined} />
              )}
            </Card>

            <Card>
              <CardHeader
                eyebrow="Assets"
                title="Generated assets"
                description="Images with their scene lineage and prompts, the composed audio and its licences, the storyboard plan, and run logs."
                actions={<ImageIcon />}
              />
              <AssetsPanel videoId={videoId} />
            </Card>
          </div>

          {/* ---- Contextual inspector ---- */}
          <aside className={styles.inspector}>
            {focus && (
              <Card className={`${styles.jobCard} ${isActive(focus) ? styles.jobCardLive : ""}`} data-testid="activity-card">
                <CardHeader
                  eyebrow={isActive(focus) ? "Running now" : "Last job"}
                  title={STAGE_LABEL[focus.stage] ?? focus.stage}
                  description={`started ${formatRelative(focus.started_at ?? focus.created_at)}${
                    focus.finished_at ? ` · finished ${formatRelative(focus.finished_at)}` : ""
                  }`}
                  actions={<StatusBadge status={focus.status} />}
                />
                {focus.message && (
                  <p className={focus.status === "FAILED" ? styles.jobError : waitingForGpu ? styles.jobWarning : styles.jobMessage}>
                    {focus.message}
                  </p>
                )}
                {isActive(focus) && (
                  <p className={styles.jobHint}>
                    Stages run in the background worker; this page polls every few seconds and refreshes the deliverable when the job ends.
                  </p>
                )}
                {focus.status === "FAILED" && (
                  <p className={styles.jobHint}>
                    Check the log below for the failing command, fix the cause, then re-run the stage. A stage that already has its outputs is reused, not regenerated.
                  </p>
                )}
                {focus.log_tail && (
                  <div>
                    <button type="button" className={styles.logToggle} onClick={() => setShowLog((v) => !v)}>
                      {showLog ? "Hide log" : "Show log"}
                    </button>
                    {showLog && <pre className={styles.log}>{focus.log_tail}</pre>}
                  </div>
                )}
              </Card>
            )}

            {(gpuQuery.data ?? []).length > 0 && (
              <Card className={styles.gpuCard}>
                <CardHeader
                  eyebrow="GPU worker"
                  title={gpuOpen ? `${gpuOpen} render${gpuOpen === 1 ? "" : "s"} on the queue` : gpuFailed.length ? "Remote render failed" : "Remote renders landed"}
                  actions={<span className={`${styles.gpuIcon} ${gpuOpen ? styles.gpuIconLive : ""}`}><GpuIcon /></span>}
                />
                {gpuLanded && (
                  <div className={styles.resume}>
                    <p className={styles.resumeText}>The images are on disk. Continue production to pick them up - nothing is regenerated.</p>
                    <StageActionButton videoId={videoId} stage="produce" label="Continue production" disabled={busy} disabledReason="Another operation is already in progress for this project." />
                  </div>
                )}
                <GpuJobsPanel videoId={videoId} compact />
              </Card>
            )}

            <Card>
              <CardHeader
                eyebrow="Review gate"
                title={status ? status.verdict.replace(/_/g, " ").toLowerCase() : "Loading…"}
                description="Recomputed live from the project's current disk state - never cached."
              />
              {statusQuery.isError && (
                <ErrorState compact title="Failed to load status" description={(statusQuery.error as Error).message} />
              )}
              {status && status.blocking.length === 0 && (
                <p className={styles.clear}>No blockers. {isReviewable(status.verdict) ? "A human decision is the next step." : ""}</p>
              )}
              {status && status.blocking.length > 0 && (
                <ul className={styles.blockers}>
                  {status.blocking.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
            </Card>

            <Card className={styles.produceCard}>
              <CardHeader
                eyebrow="Actions"
                title="Produce"
                description="Research → creative → images → audio → assemble & render in one pass, reusing whatever each stage already has."
              />
              <label className={styles.produceOption}>
                <span className={styles.produceLabel}>Image mode</span>
                <select
                  value={scenesOverride === null ? "auto" : scenesOverride ? "scenes" : "plates"}
                  onChange={(e) => setScenesOverride(e.target.value === "auto" ? null : e.target.value === "scenes")}
                  disabled={busy}
                  aria-label="Image mode"
                  className={styles.select}
                >
                  <option value="auto">Automatic</option>
                  <option value="scenes">Storyboard scenes</option>
                  <option value="plates">Single plate set</option>
                </select>
              </label>
              <div className={styles.produceAction}>
                <StageActionButton
                  videoId={videoId}
                  stage="produce"
                  label={waitingForGpu ? "Resume produce" : "Produce"}
                  params={scenesOverride === null ? {} : { scenes: scenesOverride }}
                  disabled={busy}
                  disabledReason="Another operation is already in progress for this project."
                />
              </div>
            </Card>

          </aside>
        </div>
      )}
    </div>
  );
}

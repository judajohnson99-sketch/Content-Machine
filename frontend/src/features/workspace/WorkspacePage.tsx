import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
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
import { OwnMediaPanel } from "../../components/OwnMediaPanel";
import { ReviewPanel } from "../../components/ReviewPanel";
import { DiscoverMedia } from "../../components/DiscoverMedia";
import { ResearchPanel } from "../../components/ResearchPanel";
import { ResearchInfluencePanel } from "../../components/ResearchInfluencePanel";
import { DeleteProduction } from "../../components/DeleteProduction";
import { GpuJobsPanel } from "../../components/GpuJobsPanel";
import { ProductionHeader } from "../../components/ProductionHeader";
import { BlockersPanel } from "../../components/BlockersPanel";
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
  const [search, setSearch] = useSearchParams();
  const steps = ["Goal", "Research", "Creative direction", "Media", "Produce", "Review", "Finished video"];
  const step = steps.includes(search.get("step") ?? "") ? search.get("step")! : "Produce";
  // null = automatic (scripts.project.produce_uses_scenes decides);
  // true/false = the operator's explicit override, sent as `scenes`.
  const [scenesOverride, setScenesOverride] = useState<boolean | null>(null);
  const [imageSource, setImageSource] = useState<"automatic" | "generated" | "procedural">("automatic");
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
  const fullLengthSeconds = project?.experiment?.full_length_seconds ?? null;
  // An excerpt only while it is actually shorter than what it is meant to be:
  // once it has been produced at full length the flag on disk is stale, and
  // the length that exists is the one that matters.
  const isExcerpt =
    !!fullLengthSeconds &&
    ((project?.duration_seconds ?? 0) > 0
      ? (project?.duration_seconds ?? 0) < fullLengthSeconds - 1
      : !!project?.experiment?.is_excerpt);

  return (
    <div className={styles.page}>
      <div className={styles.topBar}>
        <Link to="/" className={styles.backLink}>&larr; Dashboard</Link>
        {status && isReviewable(status.verdict) && (
          <Link to={`/review?project=${videoId}`} className={styles.reviewLink}>
            <CheckCircleIcon width={16} height={16} /> Open in Review Center
          </Link>
        )}
      </div>

      {!projectQuery.isError && (
        <ProductionHeader
          videoId={videoId}
          project={project}
          assets={assets}
          status={status}
          runs={runsQuery.data}
          compact
        />
      )}

      {projectQuery.isError && (
        <ErrorState
          title="Failed to load project"
          where={`GET /api/v1/projects/${videoId}/`}
          description={(projectQuery.error as Error).message}
          hint="A 404 means no such project directory exists under projects/. Check the id in the URL or create the production from the catalogue."
          action={<Link to="/projects/new" className={styles.reviewLink}>Start a new production</Link>}
        />
      )}

      <nav className={styles.steps} aria-label="Production steps">{steps.map((label, index) => <button key={label} type="button" aria-current={step === label ? "step" : undefined} className={`${styles.step} ${step === label ? styles.selectedStep : ""}`} onClick={() => setSearch({ step: label })}><span>{String(index + 1).padStart(2, "0")}</span>{label}</button>)}</nav>
      {projectQuery.isLoading ? (
        <SkeletonRows count={5} />
      ) : projectQuery.isError ? null : (
        <div className={styles.workstation}>
          {/* ---- Main creative canvas ---- */}
          <div className={styles.canvasCol}>
            {(step === "Produce" || step === "Finished video") && <Card padded={false} className={styles.canvasCard}>
              <div className={styles.canvasHead}>
                <CardHeader
                  eyebrow="Deliverable"
                  title={assets?.video ? "Your film, coming to life" : assets?.images.length ? "Production imagery" : "Room for your vision"}
                  description={
                    assets?.video
                      ? `Latest preview · ${formatDateTime(assets.video.modified_utc)}`
                      : assets?.images.length
                        ? `${assets.images.length} images ready to shape your film.`
                        : "Choose your media or let the creative direction guide production."
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
                    description="Your pictures, sound and finished film will appear here. Choose Produce to bring them together."
                  />
                ))}
              </div>
            </Card>}

            {step === "Produce" && <details className={styles.advanced}><summary>Production details & recovery</summary><Card>
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
            </Card></details>}

            {["Goal", "Research", "Creative direction"].includes(step) && <Card>
              <CardHeader
                eyebrow="Research"
                title="Research brief & findings"
                description="What this project should feel like, who else does it well, and what research turned up. Research can be re-run at any time; findings set pacing, movement, dissolves and the sound layers as well as the writing."
                actions={
                  <StageActionButton
                    videoId={videoId}
                    stage="research"
                    label="Run research now"
                    params={{ force: true }}
                    disabled={busy}
                    disabledReason="Another operation is already in progress for this project."
                  />
                }
              />
              <ResearchPanel
                videoId={videoId}
                disabled={busy}
                disabledReason="Another operation is already in progress for this project."
              />
            </Card>}
            {step === "Creative direction" && <Card><CardHeader eyebrow="Make it yours" title="Develop the creative direction" description="Your brief and sourced findings guide the writing, atmosphere and pacing." actions={<StageActionButton videoId={videoId} stage="creative" label="Develop creative direction" params={{ force: true }} disabled={busy} disabledReason="Wait for the current operation to finish." />} /><StageActionButton videoId={videoId} stage="storyboard" label="Build storyboard" params={{ force: true }} disabled={busy} disabledReason="Wait for the current operation to finish." /></Card>}

            {["Research", "Creative direction"].includes(step) && <Card>
              <CardHeader
                eyebrow="Research influence"
                title="What the research changed"
                description="Every production parameter sourced findings asked for, the sentences and sources behind it, and whether this build actually applied it."
              />
              <ResearchInfluencePanel videoId={videoId} />
            </Card>}

            {step === "Media" && <Card>
              <CardHeader
                eyebrow="Picture & sound"
                title="Choose the feeling"
                actions={<Link to={`/system/image-lab?project=${encodeURIComponent(videoId)}`}>Create images ↗</Link>}
                description="Browse your library, preview it, and assign it to the shots or to a sound layer. Anything you leave unassigned is generated as before."
              />
              <OwnMediaPanel
                videoId={videoId}
                disabled={busy}
                disabledReason="Another operation is already in progress for this project."
              />
              <details className={styles.advanced}><summary>Discover media for this film</summary><DiscoverMedia initialQuery={project?.experiment?.goal_text || project?.selected_title || ""} /></details>
            </Card>}

            {["Media", "Creative direction"].includes(step) && <Card>
              <CardHeader
                eyebrow="In this production"
                title="Pictures, sound & storyboard"
                description="Images with their scene lineage and prompts, the composed audio and its licences, the storyboard plan, and run logs."
                actions={<ImageIcon />}
              />
              <AssetsPanel videoId={videoId} />
            </Card>}
            {step === "Review" && <ReviewPanel videoId={videoId} />}
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
                    Your production is taking shape. You can keep browsing; the preview updates when it is ready.
                  </p>
                )}
                {focus.status === "FAILED" && (
                  <p className={styles.jobHint}>
                    Your existing work is saved. Review the issue below, then retry the operation.
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
              <details className={styles.advanced}><summary>{gpuOpen ? "Creating images" : "Image generation history"}</summary><Card className={styles.gpuCard}>
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
              </Card></details>
            )}

            <Card id="blockers">
              <CardHeader
                eyebrow="Review gate"
                title={
                  status
                    ? status.blocking.length > 0
                      ? `${status.blocking.length} thing${status.blocking.length === 1 ? "" : "s"} to settle`
                      : "Clear to review"
                    : "Loading…"
                }
                description="Resolve these before approving the finished video."
              />
              {statusQuery.isError && (
                <ErrorState compact title="Failed to load status" description={(statusQuery.error as Error).message} />
              )}
              {status && <BlockersPanel videoId={videoId} blocking={status.blocking} />}
            </Card>

            <Card className={styles.produceCard}>
              <CardHeader
                eyebrow="Actions"
                title="Produce"
                description="Bring your direction and selected media together into a finished film. Completed work is reused."
              />
              <label className={styles.produceOption}><span className={styles.produceLabel}>Visual source</span><select className={styles.select} aria-label="Visual source" value={imageSource} onChange={e => setImageSource(e.target.value as typeof imageSource)}><option value="automatic">Automatic</option><option value="generated">AI-generated imagery</option><option value="procedural">Abstract / test imagery</option></select></label>
              <p className={styles.jobHint}>Your selected library media takes priority. AI imagery waits for a capable creator computer; it is never replaced with abstract placeholders.</p>
              <details className={styles.advanced}><summary>Advanced image planning</summary><label className={styles.produceOption}>
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
              </label></details>
              <div className={styles.produceAction}>
                <StageActionButton
                  videoId={videoId}
                  stage="produce"
                  label={waitingForGpu ? "Resume produce" : "Produce"}
                  params={{ image_source: imageSource, ...(scenesOverride === null ? {} : { scenes: scenesOverride }) }}
                  disabled={busy}
                  disabledReason="Another operation is already in progress for this project."
                />
              </div>
            </Card>

            {fullLengthSeconds && (
              <Card>
                <CardHeader
                  eyebrow="Length"
                  title={isExcerpt ? "This is an excerpt" : "Full length"}
                  description={
                    isExcerpt
                      ? `What exists is ${Math.round((project?.duration_seconds ?? 0))}s of a production meant to run ${Math.round(fullLengthSeconds / 60)} minutes. Producing at full length re-plans the shots for that runtime and reuses every image already generated.`
                      : `This production runs ${Math.round(fullLengthSeconds / 60)} minutes.`
                  }
                />
                {isExcerpt && (
                  <StageActionButton
                    videoId={videoId}
                    stage="produce"
                    label={`Produce at full length (${Math.round(fullLengthSeconds / 60)} min)`}
                    params={{ duration: fullLengthSeconds }}
                    disabled={busy}
                    disabledReason="Another operation is already in progress for this project."
                  />
                )}
              </Card>
            )}

            {assets?.video && (
              <Card>
                <CardHeader
                  eyebrow="Hand-off"
                  title="Editable project"
                  description="Builds a Kdenlive project of this exact edit, with project-local media and a provenance manifest, packaged as one downloadable archive. It does not re-render."
                />
                <StageActionButton
                  videoId={videoId}
                  stage="editable"
                  label={assets.editing?.archive ? "Rebuild editable project" : "Build editable project"}
                  disabled={busy}
                  disabledReason="Another operation is already in progress for this project."
                />
              </Card>
            )}

            <details className={styles.advanced}><summary>Manage production</summary><Card id="danger-zone">
              <CardHeader eyebrow="Danger zone" title="Delete production" />
              <DeleteProduction
                videoId={videoId}
                disabled={busy || gpuOpen > 0}
                disabledReason="Wait for the current operation to finish."
              />
            </Card></details>

          </aside>
        </div>
      )}
    </div>
  );
}

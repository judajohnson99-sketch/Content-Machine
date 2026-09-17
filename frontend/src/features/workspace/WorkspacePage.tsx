import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getProject, getProjectStatus } from "../../api/projects";
import { listRuns } from "../../api/pipeline";
import { StatusBadge } from "../../components/StatusBadge";
import { StageActionButton } from "../../components/StageActionButton";
import { PipelineStageGraph } from "../../components/PipelineStageGraph";
import { DeliverablePanel } from "../../components/DeliverablePanel";
import { AssetsPanel } from "../../components/AssetsPanel";
import { PageHeader } from "../../components/ui/PageHeader";
import { Card, CardHeader } from "../../components/ui/Card";
import { ErrorState, SkeletonRows } from "../../components/ui/States";
import { isActive } from "../../types/pipeline";
import type { LatestRuns } from "../../types/pipeline";
import styles from "./WorkspacePage.module.css";

function anyStageActive(runs: LatestRuns | undefined): boolean {
  if (!runs) return false;
  return Object.values(runs).some(isActive);
}

export function WorkspacePage() {
  const { videoId } = useParams<{ videoId: string }>();
  // null = automatic (scripts.project.produce_uses_scenes decides);
  // true/false = the operator's explicit override, sent as `scenes`.
  const [scenesOverride, setScenesOverride] = useState<boolean | null>(null);

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

  if (!videoId) return <ErrorState title="No project specified" />;

  const busy = anyStageActive(runsQuery.data);

  return (
    <div>
      <PageHeader
        backTo={{ to: "/", label: "Dashboard" }}
        title={projectQuery.data ? projectQuery.data.selected_title || projectQuery.data.video_id : videoId}
        description={
          projectQuery.data?.experiment?.niche ? (
            <>
              <code>{projectQuery.data.video_id}</code> · {projectQuery.data.experiment.niche}
            </>
          ) : (
            <code>{videoId}</code>
          )
        }
      />

      {projectQuery.isError && (
        <ErrorState title="Failed to load project" description={(projectQuery.error as Error).message} />
      )}

      {projectQuery.isLoading ? (
        <SkeletonRows count={5} />
      ) : (
        <div className={styles.grid}>
          <Card>
            <CardHeader title="Review status" description="Recomputed live from the project's current disk state - never cached." />
            {statusQuery.isLoading && <SkeletonRows count={2} />}
            {statusQuery.isError && (
              <ErrorState title="Failed to load status" description={(statusQuery.error as Error).message} />
            )}
            {statusQuery.data && (
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <StatusBadge status={statusQuery.data.verdict} />
                  {statusQuery.data.stale && (
                    <span style={{ fontSize: 12, color: "var(--warning-fg)" }}>
                      stale — recorded as {statusQuery.data.recorded}
                    </span>
                  )}
                </div>
                {statusQuery.data.blocking.length > 0 && (
                  <ul style={{ margin: "10px 0 0", paddingLeft: 18, fontSize: 13, color: "var(--text-secondary)" }}>
                    {statusQuery.data.blocking.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </Card>

          <Card>
            <CardHeader
              title="Deliverable"
              description="The current render, its thumbnails, and the QC and packaging evidence behind the verdict."
            />
            <DeliverablePanel videoId={videoId} />
          </Card>

          <Card>
            <CardHeader title="Pipeline" description="Each stage's last known run, triggered here or from the CLI." />
            {runsQuery.isLoading && <SkeletonRows count={4} />}
            {runsQuery.isError && (
              <ErrorState title="Failed to load pipeline state" description={(runsQuery.error as Error).message} />
            )}
            {runsQuery.data && <PipelineStageGraph videoId={videoId} runs={runsQuery.data} projectBusy={busy} />}
          </Card>

          <Card>
            <div className={styles.produceRow}>
              <div>
                <CardHeader title="Produce" />
                <p className={styles.produceDescription}>
                  Runs research → creative → images → audio → assemble &amp; render in one pass, reusing whatever
                  each stage already has cached. Projects with narration (or an existing storyboard) get a scene
                  storyboard automatically; long static formats keep their single plate set.
                </p>
                <label className={styles.produceOption}>
                  <select
                    value={scenesOverride === null ? "auto" : scenesOverride ? "scenes" : "plates"}
                    onChange={(e) =>
                      setScenesOverride(e.target.value === "auto" ? null : e.target.value === "scenes")
                    }
                    disabled={busy}
                    aria-label="Image mode"
                  >
                    <option value="auto">Image mode: automatic</option>
                    <option value="scenes">Image mode: storyboard scenes</option>
                    <option value="plates">Image mode: single plate set</option>
                  </select>
                </label>
              </div>
              <StageActionButton
                videoId={videoId}
                stage="produce"
                label="Produce"
                params={scenesOverride === null ? {} : { scenes: scenesOverride }}
                disabled={busy}
                disabledReason="Another operation is already in progress for this project."
              />
            </div>
          </Card>

          <Card>
            <CardHeader title="Assets" description="Generated images, the composed audio and its licences, the storyboard plan, and run logs." />
            <AssetsPanel videoId={videoId} />
          </Card>
        </div>
      )}
    </div>
  );
}

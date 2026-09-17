import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getProject, getProjectStatus } from "../../api/projects";
import { listRuns } from "../../api/pipeline";
import { StatusBadge } from "../../components/StatusBadge";
import { StageActionButton } from "../../components/StageActionButton";
import { PipelineStageGraph } from "../../components/PipelineStageGraph";
import { isActive } from "../../types/pipeline";
import type { LatestRuns } from "../../types/pipeline";

function anyStageActive(runs: LatestRuns | undefined): boolean {
  if (!runs) return false;
  return Object.values(runs).some(isActive);
}

export function WorkspacePage() {
  const { videoId } = useParams<{ videoId: string }>();

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

  if (!videoId) return <p>No project specified.</p>;

  return (
    <div>
      <p>
        <Link to="/">← Dashboard</Link>
      </p>

      {projectQuery.isLoading && <p>Loading project…</p>}
      {projectQuery.isError && (
        <p role="alert">Failed to load project: {(projectQuery.error as Error).message}</p>
      )}
      {projectQuery.data && (
        <header style={{ marginBottom: 20 }}>
          <h1 style={{ margin: 0 }}>{projectQuery.data.selected_title || projectQuery.data.video_id}</h1>
          <p style={{ margin: "4px 0", color: "var(--text)" }}>
            <code>{projectQuery.data.video_id}</code>
            {projectQuery.data.experiment?.niche && ` · ${projectQuery.data.experiment.niche}`}
          </p>
        </header>
      )}

      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16 }}>Review status</h2>
        {statusQuery.isLoading && <p>Loading status…</p>}
        {statusQuery.isError && (
          <p role="alert">Failed to load status: {(statusQuery.error as Error).message}</p>
        )}
        {statusQuery.data && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <StatusBadge status={statusQuery.data.verdict} />
            {statusQuery.data.stale && (
              <span style={{ fontSize: 12, color: "#9a6700" }}>
                stale — recorded as {statusQuery.data.recorded}
              </span>
            )}
            {statusQuery.data.blocking.length > 0 && (
              <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 13, width: "100%" }}>
                {statusQuery.data.blocking.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16 }}>Pipeline</h2>
        {runsQuery.isLoading && <p>Loading pipeline state…</p>}
        {runsQuery.isError && (
          <p role="alert">Failed to load pipeline state: {(runsQuery.error as Error).message}</p>
        )}
        {runsQuery.data && (
          <PipelineStageGraph
            videoId={videoId}
            runs={runsQuery.data}
            projectBusy={anyStageActive(runsQuery.data)}
          />
        )}
      </section>

      <section>
        <h2 style={{ fontSize: 16 }}>Produce</h2>
        <p style={{ fontSize: 13, color: "var(--text)", maxWidth: 480 }}>
          Runs creative → visuals/scenes → audio → assemble &amp; render in one
          pass, reusing whatever each stage already has cached.
        </p>
        <StageActionButton
          videoId={videoId}
          stage="produce"
          label="Produce"
          disabled={anyStageActive(runsQuery.data)}
          disabledReason="Another operation is already in progress for this project."
        />
      </section>
    </div>
  );
}

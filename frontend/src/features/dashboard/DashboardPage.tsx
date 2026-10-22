import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../../api/projects";
import { getReadiness, listRecentRuns } from "../../api/system";
import { StatusBadge } from "../../components/StatusBadge";
import { PageHeader } from "../../components/ui/PageHeader";
import { Card, CardHeader } from "../../components/ui/Card";
import { EmptyState, ErrorState, SkeletonRows } from "../../components/ui/States";
import { Button } from "../../components/ui/Button";
import { ActivityIcon, CheckCircleIcon, FilmIcon, FolderIcon, GpuIcon, PlayIcon } from "../../components/ui/icons";
import { isReviewable } from "../../lib/projectStatus";
import { formatDateTime, formatRelative } from "../../lib/format";
import { fileUrl } from "../../api/assets";
import { gpuStateLabel, statusTone } from "../../lib/statusTokens";
import type { ProjectSummary } from "../../types/project";
import type { PipelineRun } from "../../types/pipeline";
import styles from "./DashboardPage.module.css";

const STAGE_LABEL: Record<string, string> = {
  research: "Research", creative: "Creative brief", storyboard: "Storyboard", scenes: "Scene images",
  audio: "Audio", visuals: "Visuals", run: "Assemble & render", produce: "Produce",
};

export function DashboardPage() {
  const [query, setQuery] = useState("");

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    refetchInterval: 20_000, // live-computed on the server, no cache (plan §9)
  });
  const activity = useQuery({ queryKey: ["recent-runs"], queryFn: listRecentRuns, refetchInterval: 8_000 });
  const readiness = useQuery({ queryKey: ["readiness"], queryFn: getReadiness, refetchInterval: 30_000 });

  const data = projects.data;
  const total = data?.length ?? 0;
  const readyForReview = data?.filter((p) => p.overall_status === "READY_FOR_REVIEW").length ?? 0;
  const needsAttention = data?.filter((p) => p.overall_status === "NEEDS_ATTENTION").length ?? 0;
  const titleOf = useMemo(() => new Map((data ?? []).map((p) => [p.video_id, p.selected_title || p.video_id])), [data]);

  const attentionQueue = useMemo(
    () => (data ?? []).filter((p) => isReviewable(p.overall_status)).slice(0, 6),
    [data],
  );

  const continueQueue = useMemo(
    () =>
      [...(data ?? [])]
        .filter((p) => !p.archived && !isReviewable(p.overall_status))
        .sort((a, b) => ((a.created_utc ?? "") < (b.created_utc ?? "") ? 1 : -1))
        .slice(0, 6),
    [data],
  );

  const recentProjects = useMemo(
    () =>
      [...(data ?? [])]
        .sort((a, b) => ((a.created_utc ?? "") < (b.created_utc ?? "") ? 1 : -1))
        .slice(0, 6),
    [data],
  );

  const failures = useMemo(
    () => (activity.data?.recent ?? []).filter((r) => r.status === "FAILED" || r.status === "NEEDS_ATTENTION").slice(0, 5),
    [activity.data],
  );
  const finished = useMemo(
    () => (activity.data?.recent ?? []).filter((r) => r.status === "SUCCEEDED").slice(0, 5),
    [activity.data],
  );

  const filtered = useMemo(() => {
    const rows = [...(data ?? [])].sort((a, b) => ((a.created_utc ?? "") < (b.created_utc ?? "") ? 1 : -1));
    if (!query.trim()) return rows;
    const q = query.trim().toLowerCase();
    return rows.filter(
      (p) =>
        p.video_id.toLowerCase().includes(q) ||
        (p.selected_title ?? "").toLowerCase().includes(q) ||
        (p.niche ?? "").toLowerCase().includes(q),
    );
  }, [data, query]);

  const gpu = readiness.data?.depicted_imagery;
  const remote = readiness.data?.remote_gpu;
  const worker = remote?.workers.find((w) => w.worker_id === remote.worker_id) ?? remote?.workers[0];
  const active = activity.data?.active ?? [];

  const summary = data
    ? [
        `${total} project${total === 1 ? "" : "s"}`,
        active.length ? `${active.length} running` : null,
        readyForReview ? `${readyForReview} ready for review` : null,
        needsAttention ? `${needsAttention} need${needsAttention === 1 ? "s" : ""} attention` : null,
      ].filter(Boolean).join(" · ")
    : "Loading…";

  return (
    <div>
      <PageHeader
        eyebrow="Your creator studio"
        title="Make something worth watching."
        description={summary}
        actions={
          <Link to="/projects/new" className={styles.primaryLink}>
            <PlayIcon width={16} height={16} /> New production
          </Link>
        }
      />

      {projects.isLoading && <SkeletonRows count={6} />}

      {projects.isError && (
        <ErrorState
          title="Failed to load projects"
          where="GET /api/v1/projects/"
          description={(projects.error as Error).message}
          hint="The API did not answer. If the server is down, start it and retry; if your session expired, reload to sign in again."
          action={
            <Button size="sm" onClick={() => projects.refetch()}>
              Retry
            </Button>
          }
        />
      )}

      {data && total === 0 && (
        <EmptyState
          icon={<FolderIcon width={26} height={26} />}
          title="No productions yet"
          description="Start from a creative direction in the catalogue. The project appears here the moment it exists and updates live as it moves through the pipeline."
          action={
            <Link to="/projects/new" className={styles.primaryLink}>
              <PlayIcon width={16} height={16} /> New production
            </Link>
          }
        />
      )}

      {data && total > 0 && (
        <>
          <section className={styles.now} aria-label="Where you left off">
            <Card className={styles.nowCard}>
              <CardHeader
                eyebrow="Pick up where you left off"
                title={continueQueue.length ? `${continueQueue.length} in progress` : "Nothing in progress"}
                actions={<span className={`${styles.nowIcon} ${active.length ? styles.nowIconLive : ""}`}><ActivityIcon /></span>}
              />
              {continueQueue.length === 0 && (
                <p className={styles.muted}>Every production is either finished or waiting on your decision.</p>
              )}
              <ul className={styles.queueList}>
                {continueQueue.map((p) => {
                  const run = active.find((r) => r.video_id === p.video_id);
                  return (
                    <li key={p.video_id}>
                      <Link to={`/projects/${p.video_id}`} className={styles.queueRow}>
                        <span className={styles.queueMain}>
                          <span className={styles.queueTitle}>{p.selected_title || p.video_id}</span>
                          <span className={styles.queueMeta}>
                            {run ? `${STAGE_LABEL[run.stage] ?? run.stage} running` : `started ${formatRelative(p.created_utc)}`}
                          </span>
                        </span>
                        <StatusBadge status={run ? run.status : p.overall_status} />
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </Card>

            <Card className={styles.nowCard}>
              <CardHeader
                eyebrow="Needs your decision"
                title={attentionQueue.length ? `${attentionQueue.length} awaiting review` : "Review queue clear"}
                actions={
                  <Link to="/review" className={styles.cardLink}>
                    Review Center &rarr;
                  </Link>
                }
              />
              {attentionQueue.length === 0 && <p className={styles.muted}>Nothing is waiting on a human decision.</p>}
              <ul className={styles.queueList}>
                {attentionQueue.map((p) => (
                  <li key={p.video_id}>
                    <Link to={`/review?project=${p.video_id}`} className={styles.queueRow}>
                      <span className={styles.queueMain}>
                        <span className={styles.queueTitle}>{p.selected_title || p.video_id}</span>
                        <span className={styles.queueMeta}>{p.niche?.replace(/_/g, " ") ?? "production"}</span>
                      </span>
                      <StatusBadge status={p.overall_status} />
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>

            <Card className={styles.nowCard}>
              <CardHeader
                eyebrow="Start something"
                title="New production"
                actions={<span className={styles.nowIcon}><PlayIcon /></span>}
              />
              <p className={styles.muted}>
                Describe what you want to make and the catalogue suggests a direction to build from.
              </p>
              <Link to="/projects/new" className={styles.primaryLink}>
                <PlayIcon width={16} height={16} /> New production
              </Link>
            </Card>
          </section>

          <section>
            <div className={styles.sectionHead}>
              <h2 className={styles.sectionTitle}>Your productions</h2>
              <span className={styles.muted}>newest first</span>
            </div>
            <input className={styles.search} type="search" placeholder="Find a production…" value={query} onChange={e => setQuery(e.target.value)} aria-label="Search productions" />
            <div className={styles.projectGrid}>
              {filtered.map((p) => (
                <ProjectTile key={p.video_id} project={p} />
              ))}
            </div>
          </section>

          <details className={styles.activityDisclosure}><summary>List view · {recentProjects.length ? total : 0} productions</summary><Card padded={false} className={styles.tableCard}>
            <div className={styles.tableHead}>
              <CardHeader eyebrow="Library" title="All projects" description={`${total} project${total === 1 ? "" : "s"} on disk.`} />
              <div className={styles.searchRow}>
                <input
                  className={styles.search}
                  type="search"
                  placeholder="Filter by title, video ID, or niche…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  aria-label="Filter projects"
                />
                <span className={styles.resultCount}>
                  {filtered.length} of {total}
                </span>
              </div>
            </div>
            <div className={styles.tableWrap}>
              <ProjectTable projects={filtered} />
            </div>
          </Card></details>

          <details className={styles.systemRegion}>
            <summary className={styles.systemSummary}>
              System activity
              {active.length > 0 && <span className={styles.systemCount}>{active.length} running</span>}
              {failures.length > 0 && <span className={styles.systemCount}>{failures.length} to look at</span>}
            </summary>
            <div className={styles.split}>
              <Card>
                <CardHeader eyebrow="Recently finished" title="Completed stages" />
                {finished.length === 0 && <p className={styles.muted}>No stage has finished through the web layer yet.</p>}
                <ul className={styles.runList}>
                  {finished.map((run) => (
                    <RunRow key={run.id} run={run} title={titleOf.get(run.video_id) ?? run.video_id} />
                  ))}
                </ul>
              </Card>

              <Card>
                <CardHeader eyebrow="Failures & blockers" title={failures.length ? `${failures.length} to look at` : "No recent failures"} />
                {failures.length === 0 && <p className={styles.muted}>Recent runs all completed. Blocked reviews appear in the queue above.</p>}
                <ul className={styles.runList}>
                  {failures.map((run) => (
                    <RunRow key={run.id} run={run} title={titleOf.get(run.video_id) ?? run.video_id} showMessage />
                  ))}
                </ul>
              </Card>
            </div>

            <Card className={styles.gpuCard}>
              <CardHeader
                eyebrow="Depicted imagery"
                title={gpu ? gpuStateLabel(gpu.state) : "Checking…"}
                actions={<span className={`${styles.nowIcon} ${styles[`tone_${gpu ? statusTone(gpu.state) : "neutral"}`]}`}><GpuIcon /></span>}
              />
              {gpu && <p className={styles.gpuDetail}>{gpu.detail}</p>}
              {worker && (
                <dl className={styles.gpuFacts}>
                  <div><dt>Worker</dt><dd>{worker.worker_id} · <StatusBadge status={worker.state} /></dd></div>
                  {worker.status.gpu && <div><dt>GPU</dt><dd>{worker.status.gpu}{worker.status.vram_total_mb ? ` · ${Math.round(worker.status.vram_total_mb / 1024)} GB` : ""}</dd></div>}
                  {worker.status.model && <div><dt>Checkpoint</dt><dd className={styles.mono}>{worker.status.model}</dd></div>}
                  {worker.last_heartbeat_at && <div><dt>Heartbeat</dt><dd>{formatRelative(worker.last_heartbeat_at)}</dd></div>}
                </dl>
              )}
              {remote && (
                <div className={styles.queueCounts}>
                  <span><strong>{remote.queue.running}</strong> rendering</span>
                  <span><strong>{remote.queue.queued}</strong> waiting</span>
                  <span><strong>{remote.queue.failed}</strong> failed</span>
                </div>
              )}
              {readiness.isError && <p className={styles.muted}>Readiness unavailable: {(readiness.error as Error).message}</p>}
            </Card>
          </details>
        </>
      )}
    </div>
  );
}

function RunRow({ run, title, showMessage = false }: { run: PipelineRun; title: string; showMessage?: boolean }) {
  const at = run.finished_at ?? run.started_at ?? run.created_at;
  return (
    <li>
      <Link to={`/projects/${run.video_id}`} className={styles.runRow}>
        <span className={styles.runMain}>
          <span className={styles.runTitle}>{title}</span>
          <span className={styles.runMeta}>
            {STAGE_LABEL[run.stage] ?? run.stage} · {formatRelative(at)}
          </span>
          {showMessage && run.message && <span className={styles.runMessage}>{run.message}</span>}
        </span>
        <StatusBadge status={run.status} />
      </Link>
    </li>
  );
}

function ProjectTile({ project }: { project: ProjectSummary }) {
  const tone = statusTone(project.overall_status);
  return (
    <Link to={`/projects/${project.video_id}`} className={`${styles.tile} ${styles[`tile_${tone}`]}`}>
      <span className={styles.tileStill}>
        {project.preview_image ? (
          <img
            className={styles.tileImage}
            src={fileUrl(project.video_id, project.preview_image)}
            alt=""
            loading="lazy"
          />
        ) : (
          <span className={styles.tileStillEmpty}>
            <FilmIcon width={20} height={20} />
          </span>
        )}
      </span>
      <span className={styles.tileBody}>
        <span className={styles.tileTop}>
          <StatusBadge status={project.overall_status} />
          <span className={styles.tileWhen}>{formatRelative(project.created_utc)}</span>
        </span>
        <span className={styles.tileTitle}>{project.selected_title || project.video_id}</span>
        <span className={styles.tileMeta}>
          {project.niche ? project.niche.replace(/_/g, " ") : "production"}
        </span>
        {isReviewable(project.overall_status) && (
          <span className={styles.tileCta}><CheckCircleIcon width={14} height={14} /> Review</span>
        )}
      </span>
    </Link>
  );
}

function ProjectTable({ projects }: { projects: ProjectSummary[] }) {
  if (projects.length === 0) {
    return <EmptyState compact title="No matches" description="No project matches that filter." />;
  }
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th>Project</th>
          <th>Niche</th>
          <th>Status</th>
          <th>Created</th>
          <th><span className={styles.srOnly}>Actions</span></th>
        </tr>
      </thead>
      <tbody>
        {projects.map((p) => (
          <tr key={p.video_id}>
            <td>
              <Link to={`/projects/${p.video_id}`} className={styles.projectLink}>
                {p.selected_title || p.video_id}
              </Link>
              <div className={styles.videoId}>{p.video_id}</div>
            </td>
            <td data-label="Niche" className={p.niche ? undefined : styles.muted}>{p.niche?.replace(/_/g, " ") ?? "—"}</td>
            <td data-label="Status">
              <StatusBadge status={p.overall_status} />
            </td>
            <td data-label="Created" className={styles.muted}>{formatDateTime(p.created_utc)}</td>
            <td data-label="Actions">
              <Link to={`/projects/${p.video_id}`} className={styles.projectLink}>Open</Link>
              <span className={styles.actionDivider}> · </span>
              <Link to={`/projects/${p.video_id}#danger-zone`} className={styles.projectLink}>Delete</Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

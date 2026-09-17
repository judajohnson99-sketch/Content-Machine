import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../../api/projects";
import { StatusBadge } from "../../components/StatusBadge";
import { PageHeader } from "../../components/ui/PageHeader";
import { StatCard } from "../../components/ui/StatCard";
import { Card, CardHeader } from "../../components/ui/Card";
import { EmptyState, ErrorState, SkeletonRows } from "../../components/ui/States";
import { Button } from "../../components/ui/Button";
import { FolderIcon } from "../../components/ui/icons";
import { isReviewable } from "../../lib/projectStatus";
import { formatDateTime, formatRelative } from "../../lib/format";
import type { ProjectSummary } from "../../types/project";
import styles from "./DashboardPage.module.css";

export function DashboardPage() {
  const [query, setQuery] = useState("");

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    refetchInterval: 20_000, // live-computed on the server, no cache (plan §9)
  });

  const total = data?.length ?? 0;
  const readyForReview = data?.filter((p) => p.overall_status === "READY_FOR_REVIEW").length ?? 0;
  const needsAttention = data?.filter((p) => p.overall_status === "NEEDS_ATTENTION").length ?? 0;
  const other = total - readyForReview - needsAttention;

  const attentionQueue = useMemo(
    () => (data ?? []).filter((p) => isReviewable(p.overall_status)).slice(0, 6),
    [data],
  );

  const recentlyCreated = useMemo(
    () =>
      [...(data ?? [])]
        .filter((p) => p.created_utc)
        .sort((a, b) => (a.created_utc! < b.created_utc! ? 1 : -1))
        .slice(0, 6),
    [data],
  );

  const filtered = useMemo(() => {
    const rows = [...(data ?? [])].sort((a, b) =>
      (a.created_utc ?? "") < (b.created_utc ?? "") ? 1 : -1,
    );
    if (!query.trim()) return rows;
    const q = query.trim().toLowerCase();
    return rows.filter(
      (p) =>
        p.video_id.toLowerCase().includes(q) ||
        (p.selected_title ?? "").toLowerCase().includes(q) ||
        (p.niche ?? "").toLowerCase().includes(q),
    );
  }, [data, query]);

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="A live view over every project on disk - nothing here is cached or re-derived beyond what the API already computed."
      />

      {isLoading && <SkeletonRows count={6} />}

      {isError && (
        <ErrorState
          title="Failed to load projects"
          description={(error as Error).message}
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      )}

      {data && total === 0 && (
        <EmptyState
          icon={<FolderIcon width={28} height={28} />}
          title="No projects yet"
          description="Create one from the CLI with ./content-machine init, then it will show up here automatically."
        />
      )}

      {data && total > 0 && (
        <>
          <div className={styles.statRow}>
            <StatCard label="Total projects" value={total} />
            <StatCard label="Ready for review" value={readyForReview} tone="success" />
            <StatCard label="Needs attention" value={needsAttention} tone="warning" />
            <StatCard label="In progress / other" value={other} />
          </div>

          <div className={styles.splitRow}>
            <Card>
              <CardHeader
                title="Needs your attention"
                description="Ready for review or blocked - the Review Center's own queue, filtered by the server's overall_status."
                actions={
                  <Link to="/review" className={styles.projectLink}>
                    Open Review Center →
                  </Link>
                }
              />
              {attentionQueue.length === 0 && (
                <p className={styles.muted}>Nothing needs attention right now.</p>
              )}
              <div className={styles.queueList}>
                {attentionQueue.map((p) => (
                  <Link key={p.video_id} to={`/review?project=${p.video_id}`} className={styles.queueRow}>
                    <div className={styles.queueMain}>
                      <div className={styles.queueTitle}>{p.selected_title || p.video_id}</div>
                      <div className={styles.queueMeta}>{p.video_id}</div>
                    </div>
                    <span className={styles.queueStatus}>
                      <StatusBadge status={p.overall_status} />
                    </span>
                  </Link>
                ))}
              </div>
            </Card>

            <Card>
              <CardHeader title="Recently created" description="Newest projects by created_utc." />
              {recentlyCreated.length === 0 && <p className={styles.muted}>No timestamps recorded yet.</p>}
              <div className={styles.queueList}>
                {recentlyCreated.map((p) => (
                  <Link key={p.video_id} to={`/projects/${p.video_id}`} className={styles.queueRow}>
                    <div className={styles.queueMain}>
                      <div className={styles.queueTitle}>{p.selected_title || p.video_id}</div>
                      <div className={styles.queueMeta}>{formatRelative(p.created_utc)}</div>
                    </div>
                  </Link>
                ))}
              </div>
            </Card>
          </div>

          <Card padded={false}>
            <div style={{ padding: "20px 20px 0" }}>
              <CardHeader title="All projects" description={`${total} project${total === 1 ? "" : "s"} on disk.`} />
            </div>
            <div style={{ padding: "0 20px" }}>
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
            <div style={{ padding: "0 20px 20px", overflowX: "auto" }}>
              <ProjectTable projects={filtered} />
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function ProjectTable({ projects }: { projects: ProjectSummary[] }) {
  if (projects.length === 0) {
    return <EmptyState title="No matches" description="No project matches that filter." />;
  }
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th>Project</th>
          <th>Niche</th>
          <th>Status</th>
          <th>Created</th>
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
            <td data-label="Niche" className={p.niche ? undefined : styles.muted}>{p.niche ?? "—"}</td>
            <td data-label="Status">
              <StatusBadge status={p.overall_status} />
            </td>
            <td data-label="Created" className={styles.muted}>{formatDateTime(p.created_utc)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

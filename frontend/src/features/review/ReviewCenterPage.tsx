import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../../api/projects";
import { StatusBadge } from "../../components/StatusBadge";
import { ReviewPanel } from "../../components/ReviewPanel";

// The verdicts scripts.project.status_report() computes that a human
// review decision is actually meaningful for - this filters the same live
// list_projects() the Dashboard reads, by the server's own overall_status
// field verbatim. No gate logic is re-derived here (architecture plan §8).
const REVIEWABLE_STATUSES = new Set(["READY_FOR_REVIEW", "NEEDS_ATTENTION"]);

export function ReviewCenterPage() {
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    refetchInterval: 20_000, // live-computed on the server, no cache (plan §9)
  });

  if (isLoading) return <p>Loading projects…</p>;
  if (isError) return <p role="alert">Failed to load projects: {(error as Error).message}</p>;

  const queue = (data ?? []).filter((p) => REVIEWABLE_STATUSES.has(p.overall_status));

  return (
    <div>
      <h1>Review Center</h1>
      {queue.length === 0 && <p>Nothing awaiting review.</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>
        {queue.map((p) => (
          <li
            key={p.video_id}
            style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12, marginBottom: 10 }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <button
                type="button"
                onClick={() => setExpanded(expanded === p.video_id ? null : p.video_id)}
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  fontSize: 14,
                  fontWeight: 600,
                  padding: 0,
                  color: "var(--accent)",
                }}
              >
                {expanded === p.video_id ? "▾" : "▸"} {p.selected_title || p.video_id}
              </button>
              <code style={{ fontSize: 12 }}>{p.video_id}</code>
              <StatusBadge status={p.overall_status} />
              <Link to={`/projects/${p.video_id}`} style={{ marginLeft: "auto", fontSize: 12 }}>
                Open workspace →
              </Link>
            </div>
            {expanded === p.video_id && <ReviewPanel videoId={p.video_id} />}
          </li>
        ))}
      </ul>
    </div>
  );
}

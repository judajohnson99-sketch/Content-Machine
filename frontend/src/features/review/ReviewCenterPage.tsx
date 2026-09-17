import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { listProjects } from "../../api/projects";
import { StatusBadge } from "../../components/StatusBadge";
import { ReviewPanel } from "../../components/ReviewPanel";
import { PageHeader } from "../../components/ui/PageHeader";
import { EmptyState, ErrorState, SkeletonRows } from "../../components/ui/States";
import { ChevronIcon, CheckCircleIcon } from "../../components/ui/icons";
import { isReviewable } from "../../lib/projectStatus";
import styles from "./ReviewCenterPage.module.css";

export function ReviewCenterPage() {
  const [searchParams] = useSearchParams();
  // Deep-linked from the Dashboard's attention queue (?project=<id>) - a
  // pure client-side convenience, nothing the backend knows about.
  const [expanded, setExpanded] = useState<string | null>(searchParams.get("project"));

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    refetchInterval: 20_000, // live-computed on the server, no cache (plan §9)
  });

  const queue = (data ?? []).filter((p) => isReviewable(p.overall_status));

  return (
    <div>
      <PageHeader
        title="Review Center"
        description="Projects whose verdict is READY_FOR_REVIEW or NEEDS_ATTENTION - approve or reject against the project's current gate state."
      />

      {isLoading && <SkeletonRows count={4} />}
      {isError && <ErrorState title="Failed to load projects" description={(error as Error).message} />}

      {data && queue.length === 0 && (
        <EmptyState
          icon={<CheckCircleIcon width={28} height={28} />}
          title="Nothing awaiting review."
          description="Every project is either still in progress or already has a final decision recorded."
        />
      )}

      {data && queue.length > 0 && (
        <ul className={styles.list}>
          {queue.map((p) => (
            <li key={p.video_id} className={styles.row}>
              <button
                type="button"
                className={styles.rowHead}
                onClick={() => setExpanded(expanded === p.video_id ? null : p.video_id)}
                aria-expanded={expanded === p.video_id}
              >
                <span className={styles.chevron}>
                  <ChevronIcon direction={expanded === p.video_id ? "down" : "right"} />
                </span>
                <span className={styles.rowMain}>
                  <div className={styles.rowTitle}>{p.selected_title || p.video_id}</div>
                  <div className={styles.rowMeta}>{p.video_id}</div>
                </span>
                <span className={styles.rowStatus}>
                  <StatusBadge status={p.overall_status} />
                </span>
              </button>
              {expanded === p.video_id && (
                <div className={styles.panelWrap}>
                  <ReviewPanel videoId={p.video_id} />
                  <p style={{ marginTop: 12 }}>
                    <Link to={`/projects/${p.video_id}`} className={styles.rowLink}>
                      Open full workspace →
                    </Link>
                  </p>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

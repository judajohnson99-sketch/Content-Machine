import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { listProjects } from "../../api/projects";
import { StatusBadge } from "../../components/StatusBadge";
import { ReviewPanel } from "../../components/ReviewPanel";
import { PageHeader } from "../../components/ui/PageHeader";
import { Card } from "../../components/ui/Card";
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
  const ready = queue.filter((p) => p.overall_status === "READY_FOR_REVIEW").length;

  return (
    <div>
      <PageHeader
        eyebrow="Human review"
        title="Review Center"
        description={
          data
            ? `${queue.length} project${queue.length === 1 ? "" : "s"} awaiting a decision · ${ready} ready, ${queue.length - ready} blocked. Media first, evidence second, decision last.`
            : "Projects whose verdict is READY_FOR_REVIEW or NEEDS_ATTENTION - approve or reject against the project's current gate state."
        }
      />

      {isLoading && <SkeletonRows count={4} />}
      {isError && (
        <ErrorState
          title="Failed to load projects"
          where="GET /api/v1/projects/"
          description={(error as Error).message}
        />
      )}

      {data && queue.length === 0 && (
        <EmptyState
          icon={<CheckCircleIcon width={26} height={26} />}
          title="Nothing awaiting review."
          description="Every project is either still in progress or already has a final decision recorded."
        />
      )}

      {data && queue.length > 0 && (
        <ul className={styles.list}>
          {queue.map((p) => {
            const open = expanded === p.video_id;
            return (
              <li key={p.video_id}>
                <Card padded={false} className={`${styles.row} ${open ? styles.rowOpen : ""}`}>
                  <button
                    type="button"
                    className={styles.rowHead}
                    onClick={() => setExpanded(open ? null : p.video_id)}
                    aria-expanded={open}
                  >
                    <span className={styles.chevron}>
                      <ChevronIcon direction={open ? "down" : "right"} />
                    </span>
                    <span className={styles.rowMain}>
                      <span className={styles.rowTitle}>{p.selected_title || p.video_id}</span>
                      <span className={styles.rowMeta}>
                        {p.video_id}
                        {p.niche ? ` · ${p.niche.replace(/_/g, " ")}` : ""}
                      </span>
                    </span>
                    <span className={styles.rowStatus}>
                      <StatusBadge status={p.overall_status} />
                    </span>
                  </button>
                  {open && (
                    <div className={styles.panelWrap}>
                      <ReviewPanel videoId={p.video_id} />
                      <p className={styles.rowFooter}>
                        <Link to={`/projects/${p.video_id}`} className={styles.rowLink}>
                          Open full workspace →
                        </Link>
                      </p>
                    </div>
                  )}
                </Card>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getProject, getProjectStatus } from "../api/projects";
import { listReviewDecisions, recordReviewDecision } from "../api/review";
import { ApiError } from "../api/client";
import { StatusBadge } from "./StatusBadge";
import { DeliverablePanel } from "./DeliverablePanel";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { InlineSpinner } from "./ui/States";
import { formatDateTime } from "../lib/format";
import type { ReviewDecisionKind } from "../types/review";
import styles from "./ReviewPanel.module.css";

interface Props {
  videoId: string;
}

// One project's review surface: live verdict/blockers, live gate_digest
// (never recomputed here - it is relayed verbatim as expected_digest, per
// architecture plan §5/§8), and the approve/reject controls. A decision is
// only ever enabled once digest_state === "MATCHES" - the same staleness
// rule scripts.project.record_review_decision() enforces server-side, so a
// disabled button here always matches what the API would otherwise refuse.
export function ReviewPanel({ videoId }: Props) {
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState("");
  const [notice, setNotice] = useState<{ kind: "conflict" | "error"; message: string } | null>(null);

  const projectQuery = useQuery({
    queryKey: ["project", videoId],
    queryFn: () => getProject(videoId),
  });
  const statusQuery = useQuery({
    queryKey: ["project-status", videoId],
    queryFn: () => getProjectStatus(videoId),
    refetchInterval: 10_000,
  });
  const historyQuery = useQuery({
    queryKey: ["review-decisions", videoId],
    queryFn: () => listReviewDecisions(videoId),
  });

  const mutation = useMutation({
    mutationFn: (decision: ReviewDecisionKind) => {
      const digest = projectQuery.data?.status?.gate_digest;
      if (!digest) throw new Error("No gate_digest available for this project yet.");
      return recordReviewDecision(videoId, decision, notes, digest);
    },
    onSuccess: () => {
      setNotice(null);
      setNotes("");
      queryClient.invalidateQueries({ queryKey: ["review-decisions", videoId] });
      queryClient.invalidateQueries({ queryKey: ["project", videoId] });
      queryClient.invalidateQueries({ queryKey: ["project-status", videoId] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError && error.status === 409) {
        setNotice({
          kind: "conflict",
          message:
            "Refused: " +
            error.message +
            " — refresh and, if needed, re-run the project so the gate is re-evaluated.",
        });
        queryClient.invalidateQueries({ queryKey: ["project", videoId] });
        queryClient.invalidateQueries({ queryKey: ["project-status", videoId] });
      } else {
        setNotice({
          kind: "error",
          message: error instanceof Error ? error.message : "Request failed.",
        });
      }
    },
  });

  if (projectQuery.isLoading || statusQuery.isLoading) return <InlineSpinner label="Loading review state…" />;
  if (projectQuery.isError) {
    return <p role="alert">Failed to load project: {(projectQuery.error as Error).message}</p>;
  }
  if (statusQuery.isError) {
    return <p role="alert">Failed to load status: {(statusQuery.error as Error).message}</p>;
  }

  const status = statusQuery.data!;
  const digestFresh = status.digest_state === "MATCHES";
  const decisionsDisabled = !digestFresh || mutation.isPending;
  let disabledReason: string | undefined;
  if (!digestFresh) {
    disabledReason =
      "The recorded gate_digest is " +
      (status.digest_state === "ABSENT" ? "absent" : "stale") +
      " — re-run this project so the gate is evaluated against its current state.";
  }
  const approveBlocked = decisionsDisabled || status.blocking.length > 0;

  return (
    <div className={styles.panel}>
      <DeliverablePanel videoId={videoId} compact />
      <div className={styles.verdictRow}>
        <StatusBadge status={status.verdict} />
        {status.stale && <span className={styles.staleNote}>stale — recorded as {status.recorded}</span>}
      </div>

      {status.blocking.length > 0 && (
        <ul className={styles.blockingList}>
          {status.blocking.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      <textarea
        className={styles.notes}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Notes (optional)"
        rows={2}
        aria-label="Review notes"
      />

      <div className={styles.actions} title={disabledReason}>
        <Button
          variant="success"
          onClick={() => mutation.mutate("approved")}
          disabled={approveBlocked}
          title={status.blocking.length > 0 ? "Cannot approve while blockers remain" : disabledReason}
        >
          Approve
        </Button>
        <Button variant="danger" onClick={() => mutation.mutate("rejected")} disabled={decisionsDisabled}>
          Reject
        </Button>
      </div>

      {notice && (
        <p role="alert" className={notice.kind === "conflict" ? styles.noticeConflict : styles.noticeError}>
          {notice.message}
        </p>
      )}

      <div>
        <h3 className={styles.historyTitle}>Decision history</h3>
        {historyQuery.isLoading && <InlineSpinner label="Loading…" />}
        {historyQuery.isError && (
          <p role="alert">Failed to load history: {(historyQuery.error as Error).message}</p>
        )}
        {historyQuery.data && historyQuery.data.length === 0 && (
          <p className={styles.emptyHistory}>No decisions recorded yet.</p>
        )}
        {historyQuery.data && historyQuery.data.length > 0 && (
          <ul className={styles.historyList}>
            {[...historyQuery.data].reverse().map((entry, i) => (
              <li key={`${entry.utc}-${i}`} className={styles.historyItem}>
                <Badge tone={entry.decision === "approved" ? "success" : "danger"}>{entry.decision}</Badge>
                <span>
                  by {entry.reviewer} <span className={styles.historyMeta}>at {formatDateTime(entry.utc)}</span>
                </span>
                {entry.notes && <span className={styles.historyNotes}>“{entry.notes}”</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

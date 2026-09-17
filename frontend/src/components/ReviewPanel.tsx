import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getProject, getProjectStatus } from "../api/projects";
import { listReviewDecisions, recordReviewDecision } from "../api/review";
import { ApiError } from "../api/client";
import { StatusBadge } from "./StatusBadge";
import type { ReviewDecisionKind } from "../types/review";

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

  if (projectQuery.isLoading || statusQuery.isLoading) return <p>Loading…</p>;
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

  return (
    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <StatusBadge status={status.verdict} />
        {status.stale && (
          <span style={{ fontSize: 12, color: "#9a6700" }}>
            stale — recorded as {status.recorded}
          </span>
        )}
      </div>

      {status.blocking.length > 0 && (
        <ul style={{ margin: "8px 0", paddingLeft: 18, fontSize: 13 }}>
          {status.blocking.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Notes (optional)"
        rows={2}
        style={{ width: "100%", maxWidth: 480, marginTop: 8, fontFamily: "inherit", fontSize: 13 }}
      />

      <div style={{ display: "flex", gap: 8, marginTop: 8 }} title={disabledReason}>
        <button
          type="button"
          onClick={() => mutation.mutate("approved")}
          disabled={decisionsDisabled || status.blocking.length > 0}
          title={status.blocking.length > 0 ? "Cannot approve while blockers remain" : disabledReason}
          style={{
            padding: "6px 14px",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: decisionsDisabled || status.blocking.length > 0 ? "var(--code-bg)" : "#1a7f37",
            color: decisionsDisabled || status.blocking.length > 0 ? "var(--text)" : "#fff",
            cursor: decisionsDisabled || status.blocking.length > 0 ? "not-allowed" : "pointer",
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          Approve
        </button>
        <button
          type="button"
          onClick={() => mutation.mutate("rejected")}
          disabled={decisionsDisabled}
          style={{
            padding: "6px 14px",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: decisionsDisabled ? "var(--code-bg)" : "#cf222e",
            color: decisionsDisabled ? "var(--text)" : "#fff",
            cursor: decisionsDisabled ? "not-allowed" : "pointer",
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          Reject
        </button>
      </div>

      {notice && (
        <p
          role="alert"
          style={{
            margin: "8px 0 0",
            fontSize: 12,
            color: notice.kind === "conflict" ? "#9a6700" : "#cf222e",
            maxWidth: 480,
          }}
        >
          {notice.message}
        </p>
      )}

      <div style={{ marginTop: 12 }}>
        <h3 style={{ fontSize: 13, margin: "0 0 4px" }}>Decision history</h3>
        {historyQuery.isLoading && <p style={{ fontSize: 12 }}>Loading…</p>}
        {historyQuery.isError && (
          <p role="alert" style={{ fontSize: 12 }}>
            Failed to load history: {(historyQuery.error as Error).message}
          </p>
        )}
        {historyQuery.data && historyQuery.data.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--text)" }}>No decisions recorded yet.</p>
        )}
        {historyQuery.data && historyQuery.data.length > 0 && (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
            {[...historyQuery.data].reverse().map((entry, i) => (
              <li key={`${entry.utc}-${i}`}>
                <strong>{entry.decision}</strong> by {entry.reviewer} at {entry.utc}
                {entry.notes && ` — ${entry.notes}`}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getProject, getProjectStatus } from "../api/projects";
import { getAssets } from "../api/assets";
import { listReviewDecisions, recordReviewDecision, recordVisualGrade } from "../api/review";
import { ApiError } from "../api/client";
import { StatusBadge } from "./StatusBadge";
import { DeliverablePanel } from "./DeliverablePanel";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { InlineSpinner } from "./ui/States";
import { ShieldIcon } from "./ui/icons";
import { formatDateTime } from "../lib/format";
import type { ReviewDecisionKind } from "../types/review";
import styles from "./ReviewPanel.module.css";

interface Props {
  videoId: string;
}

// One project's review surface, in the order a reviewer needs it: the media
// itself, the QC and packaging evidence, the visual-grade claim, then the
// decision. Live verdict/blockers and the live gate_digest are relayed
// verbatim (never recomputed here - architecture plan §5/§8). A decision is
// only enabled once digest_state === "MATCHES" - the same staleness rule
// scripts.project.record_review_decision() enforces server-side, so a
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
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}><span className={styles.stepNo}>1</span> Media &amp; evidence</h3>
        <DeliverablePanel videoId={videoId} compact />
      </section>

      <section className={styles.section}>
        <h3 className={styles.sectionTitle}><span className={styles.stepNo}>2</span> Visual grade</h3>
        <VisualGradeControl videoId={videoId} />
      </section>

      <section className={styles.section}>
        <h3 className={styles.sectionTitle}><span className={styles.stepNo}>3</span> Decision</h3>
        <div className={styles.verdictRow}>
          <StatusBadge status={status.verdict} />
          {status.stale && <span className={styles.staleNote}>stale — recorded as {status.recorded}</span>}
          {digestFresh ? (
            <span className={styles.digestOk}>gate digest matches the current disk state</span>
          ) : (
            <span className={styles.staleNote}>{disabledReason}</span>
          )}
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
      </section>

      <section className={styles.section}>
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
      </section>
    </div>
  );
}

// The human's production-grade claim. Deliberately two steps (a checkbox
// confirming the images were inspected, then the button) so it can never be
// granted by a slip - and the negative is one click, because refusing is
// always safe. The API sources the reviewer from the session; this control
// only ever sends the boolean and the notes.
function VisualGradeControl({ videoId }: { videoId: string }) {
  const queryClient = useQueryClient();
  const [confirmed, setConfirmed] = useState(false);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const assets = useQuery({ queryKey: ["project-assets", videoId], queryFn: () => getAssets(videoId) });
  const prov = assets.data?.images_provenance;
  const grade = prov?.production_grade ?? null;

  const claim = useMutation({
    mutationFn: (value: boolean) => recordVisualGrade(videoId, value, notes),
    onSuccess: () => {
      setError(null);
      setConfirmed(false);
      setNotes("");
      queryClient.invalidateQueries({ queryKey: ["project-assets", videoId] });
      queryClient.invalidateQueries({ queryKey: ["project-status", videoId] });
      queryClient.invalidateQueries({ queryKey: ["project", videoId] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : "Request failed."),
  });

  if (assets.isLoading) return <InlineSpinner label="Loading provenance…" />;
  const imageCount = assets.data?.images.length ?? 0;

  return (
    <div className={styles.grade} data-testid="visual-grade">
      <div className={styles.gradeHead}>
        <span className={styles.gradeIcon}><ShieldIcon width={16} height={16} /></span>
        <div className={styles.gradeText}>
          <span className={styles.gradeState}>
            {grade === true && <Badge tone="success">production-grade · claimed by {prov?.production_grade_claim?.reviewer ?? "a human"}</Badge>}
            {grade === false && <Badge tone="danger">not production-grade</Badge>}
            {grade === null && <Badge tone="warning">no claim recorded</Badge>}
          </span>
          <span className={styles.gradeHint}>
            {prov?.provider ? `${imageCount} image${imageCount === 1 ? "" : "s"} via ${prov.provider}. ` : ""}
            {grade === null
              ? "The gate stays closed until a person states whether these visuals are publishable. A machine only ever records the negative."
              : grade === false
                ? prov?.notes ?? "Recorded as placeholder or abstract imagery; review is blocked until real imagery replaces it."
                : `Recorded ${prov?.production_grade_claim?.utc ? formatDateTime(prov.production_grade_claim.utc) : "before claims were timestamped (CLI flag)"}${prov?.production_grade_claim?.notes ? ` — “${prov.production_grade_claim.notes}”` : ""}`}
          </span>
        </div>
      </div>
      {grade !== true && (
        <div className={styles.gradeForm}>
          <label className={styles.gradeConfirm}>
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} disabled={imageCount === 0} />
            I have inspected every image at full size and they are publishable as depicted imagery
          </label>
          <input
            className={styles.gradeNotes}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes for the claim (optional)"
            aria-label="Visual grade notes"
          />
          <div className={styles.gradeActions}>
            <Button
              variant="success"
              size="sm"
              disabled={!confirmed || claim.isPending || imageCount === 0}
              onClick={() => claim.mutate(true)}
              title={imageCount === 0 ? "No images to claim" : !confirmed ? "Confirm the inspection first" : undefined}
            >
              Mark production-grade
            </Button>
            {grade !== false && (
              <Button variant="ghost" size="sm" disabled={claim.isPending} onClick={() => claim.mutate(false)}>
                Mark not production-grade
              </Button>
            )}
          </div>
        </div>
      )}
      {grade === true && (
        <div className={styles.gradeActions}>
          <Button variant="ghost" size="sm" disabled={claim.isPending} onClick={() => claim.mutate(false)}>
            Withdraw claim
          </Button>
        </div>
      )}
      {error && <p role="alert" className={styles.noticeError}>{error}</p>}
    </div>
  );
}

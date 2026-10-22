import { useQuery } from "@tanstack/react-query";
import { getQualityReport } from "../api/quality";
import { Badge } from "./ui/Badge";
import { EmptyState, ErrorState, SkeletonRows } from "./ui/States";
import { EyeIcon } from "./ui/icons";
import type { Tone } from "../lib/statusTokens";
import type {
  EvaluationStatus, FindingSeverity, QualityEvaluation, QualityFinding, QualityReport,
  QualityVerdict, VisionMeasurements,
} from "../types/quality";
import styles from "./QualityPanel.module.css";

// What each verdict and status means, in the reviewer's words. The server
// decides them; this only names them. An evaluation that did not run is
// "Not evaluated" - deliberately never drawn like a pass.
const VERDICT: Record<QualityVerdict, { label: string; tone: Tone; line: string }> = {
  OK: { label: "Nothing measurable is wrong", tone: "success",
        line: "Every evaluation that ran found nothing for this production's intent." },
  REVIEW: { label: "Worth a look", tone: "warning",
            line: "Findings to weigh during review. They inform your judgement; they do not block it." },
  DEFECT: { label: "Objective defect", tone: "danger",
            line: "Something measurably broken was found in the delivered video. This holds review until it is fixed." },
  INCOMPLETE: { label: "Incomplete", tone: "neutral",
                line: "A required evaluation could not run, so this assessment does not cover everything." },
};

const STATUS: Record<EvaluationStatus, { label: string; tone: Tone }> = {
  PASS: { label: "Nothing found", tone: "success" },
  PARTIAL: { label: "Partly evaluated", tone: "info" },
  REVIEW: { label: "Review", tone: "warning" },
  DEFECT: { label: "Defect", tone: "danger" },
  UNAVAILABLE: { label: "Not evaluated", tone: "neutral" },
  NOT_APPLICABLE: { label: "Not applicable", tone: "neutral" },
};

const SEVERITY: Record<FindingSeverity, { label: string; tone: Tone }> = {
  block: { label: "defect", tone: "danger" },
  warn: { label: "review", tone: "warning" },
  info: { label: "note", tone: "info" },
};

export function formatTimestamp(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

const DIMENSION_LABEL: Record<string, string> = {
  adherence: "prompt match",
  composition: "composition",
  artifacts: "artifacts",
  consistency: "consistency",
};

function Finding({ finding }: { finding: QualityFinding }) {
  const severity = SEVERITY[finding.severity];
  return (
    <li className={styles.finding} data-severity={finding.severity}>
      <Badge tone={severity.tone}>{severity.label}</Badge>
      {finding.dimension && (
        <span className={styles.dimension}>{DIMENSION_LABEL[finding.dimension] ?? finding.dimension}</span>
      )}
      {finding.at_seconds != null && (
        <span className={styles.time} title="Position in the video">
          {formatTimestamp(finding.at_seconds)}
        </span>
      )}
      <span className={styles.detail}>{finding.detail}</span>
    </li>
  );
}

// What the local vision model made of the images, dimension by dimension,
// and how far to trust it. The numbers are the model's scores, not
// measurements: until a model has been calibrated against labelled examples
// that is said here, beside the scores, not left to a document.
function VisionDetails({ measurements }: { measurements: Record<string, unknown> }) {
  const m = measurements as VisionMeasurements;
  if (!m.dimensions && !m.coverage) return null;
  const dims = m.dimensions ?? {};
  const coverage = m.coverage;
  const uncalibrated = Object.entries(m.models ?? {}).filter(([, state]) => state.uncalibrated.length > 0);
  const chips: string[] = [];
  if (dims.adherence) chips.push(`Prompt match ${dims.adherence.mean.toFixed(1)}/5`);
  if (dims.composition) chips.push(`Composition ${dims.composition.mean.toFixed(1)}/5`);
  if (dims.artifacts) {
    const n = dims.artifacts.images_with_artifacts;
    chips.push(n === 0 ? "No artifacts named" : `Artifacts in ${n} image${n === 1 ? "" : "s"}`);
  }
  if (dims.consistency) {
    chips.push(dims.consistency.assessed
      ? (dims.consistency.outliers ? `${dims.consistency.outliers} off-style` : "Consistent style")
      : "Consistency not assessed");
  }
  const sources = Object.entries(coverage?.sources ?? {}).map(([k, n]) => `${n} ${k}`).join(", ");
  return (
    <div className={styles.vision} data-testid="vision-details">
      {chips.length > 0 && (
        <ul className={styles.chips}>
          {chips.map((c) => <li key={c} className={styles.chip}>{c}</li>)}
        </ul>
      )}
      {coverage && (
        <p className={styles.coverageLine}>
          Judged {coverage.judged} of {coverage.eligible} depicted image{coverage.eligible === 1 ? "" : "s"}
          {sources && ` (${sources})`}
          {!!coverage.abstract_plates_not_judged &&
            ` · ${coverage.abstract_plates_not_judged} abstract plate${coverage.abstract_plates_not_judged === 1 ? "" : "s"} not judged`}
          .
        </p>
      )}
      {(coverage?.skipped ?? []).map((s) => (
        <p key={`${s.code}-${s.detail}`} className={styles.skipped}>
          {s.count} not judged: {s.detail}{s.origin !== "this host" ? ` (${s.origin})` : ""}
        </p>
      ))}
      {uncalibrated.length > 0 && (
        <p className={styles.uncalibrated}>
          {uncalibrated.map(([name, state]) => `${name} (${state.uncalibrated.map((d) => DIMENSION_LABEL[d] ?? d).join(", ")})`).join("; ")}
          {" "}has not been calibrated against labelled examples: treat those scores as a second opinion, not a measurement.
        </p>
      )}
    </div>
  );
}

function Evaluation({ evaluation }: { evaluation: QualityEvaluation }) {
  const status = STATUS[evaluation.status];
  const quiet = evaluation.status === "UNAVAILABLE" || evaluation.status === "NOT_APPLICABLE";
  return (
    <li className={styles.evaluation} data-testid={`evaluation-${evaluation.id}`}>
      <div className={styles.evalHead}>
        <span className={styles.evalTitle}>
          {evaluation.title}
          {!evaluation.required && <span className={styles.optional}> · optional</span>}
        </span>
        <Badge tone={status.tone} data-status={evaluation.status}>{status.label}</Badge>
      </div>
      <p className={quiet ? styles.reason : styles.summary}>{evaluation.summary}</p>
      {evaluation.id === "scene_vision" && !quiet && <VisionDetails measurements={evaluation.measurements} />}
      {evaluation.findings.length > 0 && (
        <ul className={styles.findings}>
          {evaluation.findings.map((f, i) => <Finding key={`${f.code}-${i}`} finding={f} />)}
        </ul>
      )}
      {evaluation.method && (
        <details className={styles.method}>
          <summary>How this was measured</summary>
          <p>{evaluation.method}</p>
        </details>
      )}
    </li>
  );
}

function Intent({ report }: { report: QualityReport }) {
  const { intent } = report;
  const key = intent.expected_key.value
    ? `${intent.expected_key.value}-key (${intent.expected_key.evidence.join(", ")})`
    : null;
  return (
    <p className={styles.intent}>
      Judged as <strong>{intent.visual_profile.label}</strong>
      {" "}({intent.visual_profile.source}), heard as{" "}
      <strong>{intent.listening_context.value.replace(/_/g, " ")}</strong>
      {intent.palette.terms.length > 0 && <> · palette {intent.palette.terms.join(", ")}</>}
      {key && <> · lighting {key}</>}
      {intent.holds_one_frame && <> · one held frame</>}.
    </p>
  );
}

// The perceptual quality assessment of the current render: whether it looks
// and sounds right for what it is meant to be, with where in the video each
// finding is and how each was measured. Evidence for the reviewer - it
// never records a verdict and never replaces the human grades below it.
export function QualityPanel({ videoId, assessedAt }: { videoId: string; assessedAt?: string | null }) {
  const query = useQuery({
    queryKey: ["quality-report", videoId, assessedAt ?? null],
    queryFn: () => getQualityReport(videoId),
    enabled: !!videoId,
  });

  if (query.isLoading) return <SkeletonRows count={3} />;
  if (query.isError) {
    return (
      <ErrorState compact title="Failed to load the quality assessment"
        where={`GET /api/v1/projects/${videoId}/quality-report/`}
        description={(query.error as Error).message} />
    );
  }

  const report = query.data;
  if (!report) {
    return (
      <EmptyState
        compact
        icon={<EyeIcon width={22} height={22} />}
        title="Not evaluated yet"
        description="Perceptual quality is assessed every time the video is rendered. Render it with Assemble & Render, or re-assess an existing render."
      />
    );
  }

  // An OK verdict covers what ran. When something did not run (or ran only
  // partly) the badge must not look like a clean pass for the whole.
  const gaps = report.coverage.unavailable.length + (report.coverage.partial ?? []).length;
  const verdict = report.verdict === "OK" && gaps > 0
    ? { label: "Nothing wrong in what was evaluated", tone: "neutral" as Tone,
        line: `${gaps} evaluation${gaps === 1 ? " was" : "s were"} not run or only partly run, so this is not a pass for ${gaps === 1 ? "it" : "them"}.` }
    : VERDICT[report.verdict];
  const counted = report.counts.block + report.counts.warn + report.counts.info;
  return (
    <div className={styles.panel} data-testid="quality-panel">
      {!report.current && (
        <p className={styles.stale} role="status">
          This assessment describes an earlier render - the video has changed since. Re-assess before relying on it.
        </p>
      )}
      <div className={styles.verdict}>
        <Badge tone={verdict.tone} data-status={report.verdict}>{verdict.label}</Badge>
        <span className={styles.verdictLine}>{verdict.line}</span>
      </div>
      {counted > 0 && (
        <p className={styles.counts}>
          {report.counts.block > 0 && <>{report.counts.block} defect{report.counts.block === 1 ? "" : "s"} · </>}
          {report.counts.warn} to review · {report.counts.info} note{report.counts.info === 1 ? "" : "s"}
        </p>
      )}
      {report.coverage.unavailable.length > 0 && (
        <p className={styles.coverage}>
          Not evaluated: {report.coverage.unavailable.map((u) => u.title).join("; ")}.
        </p>
      )}
      {(report.coverage.partial ?? []).length > 0 && (
        <p className={styles.coverage} data-testid="partial-coverage">
          Partly evaluated: {(report.coverage.partial ?? []).map((u) => u.title).join("; ")}.
        </p>
      )}
      <Intent report={report} />
      <ul className={styles.evaluations}>
        {report.evaluations.map((e) => <Evaluation key={e.id} evaluation={e} />)}
      </ul>
      <p className={styles.note}>{report.note}</p>
    </div>
  );
}

// Mirrors scripts/quality.py's report exactly - the domain module is the
// source of truth for what every status and severity means.

export type QualityVerdict = "OK" | "REVIEW" | "DEFECT" | "INCOMPLETE";
export type EvaluationStatus = "PASS" | "REVIEW" | "DEFECT" | "UNAVAILABLE" | "NOT_APPLICABLE";
export type FindingSeverity = "block" | "warn" | "info";

export interface QualityCounts {
  block: number;
  warn: number;
  info: number;
}

export interface QualityFinding {
  code: string;
  severity: FindingSeverity;
  detail: string;
  at_seconds?: number;
  scene_ids?: string[];
  /** Present on the one line that stands in for findings not listed. */
  unlisted?: number;
}

export interface QualityEvaluation {
  id: string;
  title: string;
  required: boolean;
  status: EvaluationStatus;
  summary: string;
  method: string | null;
  findings: QualityFinding[];
  measurements: Record<string, unknown>;
}

export interface QualityIntent {
  listening_context: { value: string; source: string };
  visual_profile: { value: string; label: string; source: string };
  expected_key: { value: "low" | "high" | null; evidence: string[] };
  palette: { terms: string[]; source: string | null; unresolved: string[] };
  holds_one_frame: boolean;
}

export interface QualityReport {
  video_id: string;
  generated_utc: string;
  verdict: QualityVerdict;
  counts: QualityCounts;
  coverage: {
    evaluated: string[];
    unavailable: { id: string; title: string; required: boolean; reason: string }[];
    not_applicable: { id: string; title: string; reason: string }[];
  };
  intent: QualityIntent;
  evaluations: QualityEvaluation[];
  note: string;
  /** Whether the report still describes the video on disk. */
  current: boolean;
}

/** quality.summary(), carried on the status report. */
export interface QualitySummary {
  assessed: boolean;
  verdict: QualityVerdict | null;
  current: boolean | null;
  counts: QualityCounts | null;
  generated_utc?: string;
  unavailable: string[];
}

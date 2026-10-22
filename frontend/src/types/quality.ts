// Mirrors scripts/quality.py's report exactly - the domain module is the
// source of truth for what every status and severity means.

export type QualityVerdict = "OK" | "REVIEW" | "DEFECT" | "INCOMPLETE";
export type EvaluationStatus =
  | "PASS" | "PARTIAL" | "REVIEW" | "DEFECT" | "UNAVAILABLE" | "NOT_APPLICABLE";
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
  /** Which vision dimension a finding belongs to (adherence, composition,
   *  artifacts, consistency); absent on every other evaluation. */
  dimension?: string;
  /** Present on the one line that stands in for findings not listed. */
  unlisted?: number;
}

export interface QualityEvaluation {
  id: string;
  title: string;
  required: boolean;
  status: EvaluationStatus;
  /** Evaluated, but only some of what it covers could be. */
  partial?: boolean;
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
    partial?: { id: string; title: string; reason: string }[];
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

/** The shape of the scene_vision evaluation's measurements. Everything is
 *  optional on purpose: it is read defensively from `Record<string, unknown>`. */
export interface VisionMeasurements {
  dimensions?: {
    adherence?: { mean: number; min: number; flagged: number };
    composition?: { mean: number; min: number; flagged: number };
    artifacts?: { images_with_artifacts: number; by_name: Record<string, number> };
    consistency?: { assessed: boolean; reason?: string; compared?: number; outliers?: number };
  };
  coverage?: {
    eligible: number;
    judged: number;
    abstract_plates_not_judged?: number;
    sources?: Record<string, number>;
    skipped?: { code: string; detail: string; origin: string; count: number }[];
  };
  /** Model name -> which dimensions were calibrated against labelled examples, and which were not. */
  models?: Record<string, { calibrated: string[]; uncalibrated: string[] }>;
}

// Mirrors apps/review/serializers.py exactly - the API is the source of
// truth for these shapes.

export type ReviewDecisionKind = "approved" | "rejected";

export interface ReviewDecision {
  utc: string;
  reviewer: string;
  decision: ReviewDecisionKind;
  notes: string;
  gate_digest: string;
}

export interface VisualGradeClaim {
  utc: string;
  reviewer: string;
  notes: string;
  asset_count: number;
  production_grade: boolean;
}

export interface AudioGradeClaim {
  utc: string;
  reviewer: string;
  notes: string;
  production_grade: boolean;
}

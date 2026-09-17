// Mirrors apps/review/serializers.py exactly - the API is the source of
// truth for this shape.

export type ReviewDecisionKind = "approved" | "rejected";

export interface ReviewDecision {
  utc: string;
  reviewer: string;
  decision: ReviewDecisionKind;
  notes: string;
  gate_digest: string;
}

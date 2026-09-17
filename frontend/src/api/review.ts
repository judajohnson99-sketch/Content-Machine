import { apiGet, apiPost } from "./client";
import type { ReviewDecision, ReviewDecisionKind } from "../types/review";

export function listReviewDecisions(videoId: string): Promise<ReviewDecision[]> {
  return apiGet<ReviewDecision[]>(`/projects/${videoId}/review-decisions/`);
}

// reviewer is deliberately not a parameter here: the API sources it from
// the authenticated session (request.user), never the request body
// (architecture plan §5) - this function cannot even offer to send one.
export function recordReviewDecision(
  videoId: string,
  decision: ReviewDecisionKind,
  notes: string,
  expectedDigest: string,
): Promise<ReviewDecision> {
  return apiPost<ReviewDecision>(`/projects/${videoId}/review-decisions/`, {
    decision,
    notes,
    expected_digest: expectedDigest,
  });
}

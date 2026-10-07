import { apiGet, apiPost } from "./client";
import type {
  AudioGradeClaim, ReviewDecision, ReviewDecisionKind, VisualGradeClaim,
} from "../types/review";

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

// The human's production-grade claim for the visuals. Same rule: the
// reviewer is the session user, and the boolean is always sent explicitly -
// there is no call shape that "defaults" the claim to true.
export function recordVisualGrade(
  videoId: string,
  productionGrade: boolean,
  notes: string,
): Promise<VisualGradeClaim> {
  return apiPost<VisualGradeClaim>(`/projects/${videoId}/visual-grade/`, {
    production_grade: productionGrade,
    notes,
  });
}

// The human's verdict on the audio. Only a person can answer whether a
// synthesised track is music worth listening to, so this is the one route
// by which that answer reaches provenance - the reviewer is the session
// user, exactly as for the visual claim.
export function recordAudioGrade(
  videoId: string,
  productionGrade: boolean,
  notes: string,
): Promise<AudioGradeClaim> {
  return apiPost<AudioGradeClaim>(`/projects/${videoId}/audio-grade/`, {
    production_grade: productionGrade,
    notes,
  });
}

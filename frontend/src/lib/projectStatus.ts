// The verdicts scripts.project.status_report()/list_projects() compute
// that a human review decision is actually meaningful for. Shared between
// the Dashboard's attention queue and the Review Center's queue so the two
// never drift into filtering differently - no gate logic is re-derived
// here, only a filter over the server's own overall_status field
// (architecture plan §8).
export const REVIEWABLE_STATUSES = new Set(["READY_FOR_REVIEW", "NEEDS_ATTENTION"]);

export function isReviewable(status: string): boolean {
  return REVIEWABLE_STATUSES.has(status);
}

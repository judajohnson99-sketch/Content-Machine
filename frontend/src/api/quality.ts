import { apiGet, ApiError } from "./client";
import type { QualityReport } from "../types/quality";

// A render that was never assessed is a normal state (404), not an error:
// callers get null and say "not evaluated" - never a pass.
export async function getQualityReport(videoId: string): Promise<QualityReport | null> {
  try {
    return await apiGet<QualityReport>(`/projects/${videoId}/quality-report/`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

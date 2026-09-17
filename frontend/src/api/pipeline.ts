import { apiGet, apiPost } from "./client";
import type { LatestRuns, PipelineRun, StageName } from "../types/pipeline";

export function listRuns(videoId: string): Promise<LatestRuns> {
  return apiGet<LatestRuns>(`/projects/${videoId}/pipeline-runs/`);
}

// `params` is loosely typed here on purpose: StageParams (types/pipeline.ts)
// documents each stage's real shape for call sites to author against, but
// validation of what a stage actually accepts is the DRF serializer's job
// (apps/pipeline/serializers.py) - this function must not re-implement it.
export function triggerStage(
  videoId: string,
  stage: StageName,
  clientRequestId: string,
  params: Record<string, unknown> = {},
): Promise<PipelineRun> {
  return apiPost<PipelineRun>(`/projects/${videoId}/${stage}/`, {
    client_request_id: clientRequestId,
    ...params,
  });
}

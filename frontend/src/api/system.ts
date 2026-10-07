import { apiGet, apiPost, API_BASE_URL } from "./client";
import type { GpuJob, HostReadiness } from "../types/system";
import type { PipelineRun } from "../types/pipeline";

export function getReadiness(): Promise<HostReadiness> {
  return apiGet<HostReadiness>("/system/readiness/");
}

export function listGpuJobs(projectId?: string): Promise<GpuJob[]> {
  const suffix = projectId ? `?project=${encodeURIComponent(projectId)}` : "";
  return apiGet<GpuJob[]>(`/system/gpu-jobs/${suffix}`);
}

export function listProjectGpuJobs(videoId: string): Promise<GpuJob[]> {
  return apiGet<GpuJob[]>(`/projects/${encodeURIComponent(videoId)}/gpu-jobs/`);
}

export interface GpuJobEnqueuePayload {
  prompt: string;
  negative_prompt?: string | null;
  width?: number;
  height?: number;
  count?: number;
  model?: string | null;
  seed?: number;
}

// No workflow_path here on purpose: an untested template stays a CLI-only
// capability (`worker enqueue --workflow`), never reachable from the
// dashboard.
export function enqueueGpuJob(payload: GpuJobEnqueuePayload): Promise<GpuJob> {
  return apiPost<GpuJob>("/system/gpu-jobs/", payload);
}

// Retry: the operator's decision to put a FAILED or CANCELLED job back in
// the queue. The control plane refuses any other state (409).
export function requeueGpuJob(jobId: string): Promise<GpuJob> {
  return apiPost<GpuJob>(`/system/gpu-jobs/${encodeURIComponent(jobId)}/requeue/`, {});
}

export function gpuJobAssetUrl(jobId: string, index: number): string {
  return `${API_BASE_URL}/system/gpu-jobs/${encodeURIComponent(jobId)}/assets/${index}/`;
}

export interface RecentRuns {
  active: PipelineRun[];
  recent: PipelineRun[];
}

export function listRecentRuns(): Promise<RecentRuns> {
  return apiGet<RecentRuns>("/pipeline-runs/");
}

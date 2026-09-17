import { apiGet } from "./client";
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

export interface RecentRuns {
  active: PipelineRun[];
  recent: PipelineRun[];
}

export function listRecentRuns(): Promise<RecentRuns> {
  return apiGet<RecentRuns>("/pipeline-runs/");
}

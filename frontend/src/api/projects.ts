import { apiDelete, apiGet } from "./client";
import type { ProjectDetail, ProjectSummary, StatusReport } from "../types/project";

export function listProjects(): Promise<ProjectSummary[]> {
  return apiGet<ProjectSummary[]>("/projects/");
}

export function getProject(videoId: string): Promise<ProjectDetail> {
  return apiGet<ProjectDetail>(`/projects/${videoId}/`);
}

export function getProjectStatus(videoId: string): Promise<StatusReport> {
  return apiGet<StatusReport>(`/projects/${videoId}/status/`);
}

// Permanent, with no undo: the body echoes the id being destroyed, which is
// what scripts.project.delete_project and the serializer both require.
export interface DeleteResult {
  video_id: string;
  removed: string[];
  actor: string;
  reason: string;
  deleted_utc: string;
}

export function deleteProject(
  videoId: string, reason = "",
): Promise<DeleteResult> {
  return apiDelete<DeleteResult>(`/projects/${videoId}/`, {
    confirm_video_id: videoId,
    reason,
  });
}

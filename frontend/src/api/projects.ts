import { apiGet } from "./client";
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

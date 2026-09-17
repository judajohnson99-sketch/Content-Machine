import { API_BASE_URL, apiGet } from "./client";
import type { ProjectAssets } from "../types/assets";

export function getAssets(videoId: string): Promise<ProjectAssets> {
  return apiGet<ProjectAssets>(`/projects/${videoId}/assets/`);
}

// A browser-fetchable URL for one project-relative asset path. The session
// cookie rides along because the API and the page share a site (see
// vite.config.ts), so <video>/<img> elements can load it directly. Which
// paths are servable at all is scripts.project.project_file_path()'s rule;
// this only builds the URL.
export function fileUrl(videoId: string, path: string): string {
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  return `${API_BASE_URL}/projects/${encodeURIComponent(videoId)}/files/${encoded}`;
}

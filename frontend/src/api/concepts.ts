import { apiGet, apiPost } from "./client";
import type { ConceptCatalog, CreateProjectRequest } from "../types/concepts";
import type { ProjectSummary } from "../types/project";

export function listConcepts(): Promise<ConceptCatalog> {
  return apiGet<ConceptCatalog>("/concepts/");
}

// POST /projects/ scaffolds the project (the same operation as
// `experiment.py scaffold`); starting production is a separate, idempotent
// stage trigger (triggerStage(id, "produce", ...)) so the two failures -
// "couldn't create" and "couldn't start" - stay distinguishable.
export function createProject(body: CreateProjectRequest): Promise<ProjectSummary> {
  return apiPost<ProjectSummary>("/projects/", body);
}

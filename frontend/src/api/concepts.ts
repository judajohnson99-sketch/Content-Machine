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

// A production from a sentence: the server derives the concept, scaffolds
// the project and writes the research brief every production carries.
// Nothing is produced yet - starting the run is the usual produce trigger.
export interface GoalPlan {
  title_pattern: string;
  tagline: string;
  niche: string;
  content_format: string;
  target_audience: string;
  creative_intent: string;
  visual_concept: string;
  audio_concept: string;
  shape: string;
  narration: "narrated" | "silent";
  needs_depicted_imagery: boolean;
  needs_factual_research: boolean;
  likes: string[];
  dislikes: string[];
  research_topics: string[];
  assumptions: string[];
  minutes: number | null;
}

export interface GoalResult {
  project: ProjectSummary;
  plan: GoalPlan;
  concept_id: string;
  brief: Record<string, unknown>;
}

export function createFromGoal(body: {
  goal: string;
  video_id?: string | null;
  minutes?: number | null;
  excerpt_seconds?: number | null;
}): Promise<GoalResult> {
  return apiPost<GoalResult>("/projects/from-goal/", body);
}

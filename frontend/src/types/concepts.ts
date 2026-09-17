// Mirrors scripts.experiment.concept_catalog() exactly - the domain read
// model is the contract; GET /api/v1/concepts/ returns it verbatim.
import type { HostReadiness } from "./system";

export type Readiness = "ok" | "partial" | "blocked" | "n/a";

// "reference": the curated showcase set the channel would actually publish.
// "experiment": the ranked information-value batch. "technical": regression
// concepts (dark-screen noise beds) kept for the pipeline's own sake.
export type ConceptKind = "reference" | "experiment" | "technical";

export interface ConceptReadiness {
  images: Readiness;
  images_via?: string | null;
  audio: Readiness;
  research: Readiness;
  runnable_now: boolean;
  waits_for_gpu?: boolean;
  notes: string[];
}

export interface VisualDirection {
  atmosphere?: string;
  palette?: string[];
  motifs?: string[];
  avoid?: string[];
  prompt_core?: string;
  negative?: string;
}

export interface ConceptSummary {
  id: string;
  kind?: ConceptKind;
  niche: string;
  title_pattern: string | null;
  tagline?: string | null;
  creative_intent?: string | null;
  visual_direction?: VisualDirection;
  narration?: "narrated" | "none";
  preview_seconds?: number | null;
  content_format: string;
  target_audience: string;
  video_length_minutes: number;
  visual_concept: string;
  audio_concept: string;
  audio_requirement: string;
  procedural_visuals_acceptable: boolean;
  requires_subject_research: boolean;
  production_complexity: number;
  risks: string[];
  score: number;
  readiness: ConceptReadiness;
}

export type HostCapabilities = HostReadiness;

export interface ConceptCatalog {
  capabilities: HostCapabilities;
  concepts: ConceptSummary[];
}

export interface CreateProjectRequest {
  video_id: string;
  concept_id: string;
  duration?: number | null;
}

export function conceptKind(concept: ConceptSummary): ConceptKind {
  return concept.kind ?? "experiment";
}

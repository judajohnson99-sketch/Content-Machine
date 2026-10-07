// Mirrors scripts/research.py's brief/finding shapes exactly (BRIEF_FIELDS,
// FINDING_TOPICS, FINDING_KINDS) - the domain layer is the source of truth
// for what these mean; this file only declares them on the TS side.

export type SeedReferenceType = "channel" | "video";

export interface SeedReference {
  type: SeedReferenceType;
  value: string;
}

export interface ResearchBrief {
  video_id: string;
  niche: string;
  creative_intent: string | null;
  likes: string[];
  dislikes: string[];
  seed_references: SeedReference[];
  notes: string | null;
  updated_utc: string;
}

export type FindingTopic =
  | "concept" | "visuals" | "audio" | "pacing" | "titles" | "thumbnails"
  | "audience" | "editing" | "duration" | "presentation" | "business";

export type FindingKind = "observation" | "interpretation" | "hypothesis";

export interface Finding {
  finding_id: string;
  kind: FindingKind;
  topic: FindingTopic;
  statement: string;
  source_url: string | null;
  source_title: string | null;
  confidence: "VERIFIED" | "INFERRED" | "UNVERIFIED";
  derived_utc: string;
}

export interface FindingsArtifact {
  video_id: string;
  brief_niche: string | null;
  provider: string;
  researched_utc: string;
  findings: Finding[];
}


// GET /projects/{id}/research/influence/ - what sourced research changed
// about this production. `decisions` is every directive the findings
// produced, with the finding ids and URLs behind it; `applied` is what the
// build actually used, which is deliberately the smaller set.
export interface ResearchDecision {
  parameter: string;
  value: unknown;
  rationale: string;
  evidence_finding_ids: string[];
  source_urls: string[];
  confidence: "VERIFIED" | "INFERRED" | "UNVERIFIED";
}

export interface ResearchInfluence {
  video_id: string;
  recorded_utc: string | null;
  researched?: boolean;
  findings_researched_utc: string | null;
  decisions: ResearchDecision[];
  applied: Record<string, unknown>;
  suggested_not_applied: string[];
  scene_count: number | null;
  timeline_seconds: number | null;
}

// Mirrors apps/projects/serializers.py exactly - the API is the source of
// truth for these shapes, this is just the TS-side declaration of it.

export interface ProjectSummary {
  video_id: string;
  selected_title: string | null;
  concept_id: string | null;
  niche: string | null;
  overall_status: string;
  created_utc: string | null;
  archived?: boolean;
  /** A project-relative still (thumbnail, scene image or staged media), or
   *  null when the production has produced no picture yet. */
  preview_image?: string | null;
}

export interface StatusReport {
  video_id: string;
  recorded: string;
  verdict: string;
  blocking: string[];
  stale: boolean | null;
  digest_state: string | null;
}

// GET /projects/{id}/ returns metadata.json verbatim - there is no fixed
// serializer contract for it (architecture plan §12), so only the fields
// the Workspace header actually displays are declared; everything else is
// read, never re-derived, straight off this object.
export interface ProjectDetail {
  video_id: string;
  selected_title: string | null;
  duration_seconds?: number | null;
  experiment?: {
    concept_id?: string | null;
    niche?: string | null;
    // Set on productions derived from a goal: what the operator asked for,
    // and whether what exists right now is only an excerpt of it.
    goal_text?: string | null;
    full_length_seconds?: number | null;
    is_excerpt?: boolean | null;
  } | null;
  // gate_digest is the last-recorded fingerprint of what a verdict was
  // computed from (scripts/project.py's gate_digest()) - the Review Center
  // sends it back verbatim as expected_digest; it is never recomputed or
  // guessed client-side (architecture plan §5/§8).
  // The other keys are per-stage outcomes scripts.project records as
  // it goes (render, audio, visuals, scenes, storyboard, subject_research…).
  status?: ({ overall?: string; gate_digest?: string | null } & Record<string, string | null | undefined>) | null;
}

// Mirrors scripts/visual_assets.py: one ranked list of every way a scene could
// get its picture - the owner's library, stock photography and footage, and a
// planned generation. The domain is the contract; nothing here decides
// whether media may be used.

export type CandidateSource = "library" | "stock" | "generated" | "cloud";
export type PolicyAction = "auto" | "approval_required" | "blocked";
export type Confidence = "low" | "medium" | "high";

export interface RankingFactor {
  id: string;
  label: string;
  score: number | null;
  weight: number;
  detail: string;
  applied: boolean;
}

export interface Ranking {
  candidate_id: string;
  rank: number;
  score: number;
  eligible: boolean;
  ready: boolean;
  recommended: boolean;
  confidence: Confidence;
  confidence_reasons: string[];
  summary: string;
  factors: RankingFactor[];
  limitations: string[];
  advisory: string[];
  alternatives: { candidate_id: string; source: CandidateSource; why: string }[];
}

export interface CandidateRights {
  source?: string | null;
  license?: string | null;
  license_url?: string | null;
  creator?: string | null;
  attribution_required?: boolean | null;
  attribution_text?: string | null;
  eligibility?: "eligible" | "review_required" | "ineligible";
  eligibility_reasons?: string[];
  status?: string | null;
  retrieved_utc?: string | null;
  provider_terms?: { restrictions?: string[] };
}

export interface VisualCandidate {
  candidate_id: string;
  source: CandidateSource;
  provider: string;
  media_kind: "image" | "video";
  title: string;
  description: string;
  tags: string[];
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  creator?: { name: string | null; url: string | null };
  source_url: string | null;
  preview_url: string | null;
  rights: CandidateRights;
  state: "available" | "deferred" | "unavailable" | "authorization_required";
  state_detail?: string;
  generation?: { prompt: string; negative_prompt: string; seed: number; width: number; height: number };
  near_duplicates?: string[];
  library?: { asset_id: string; usage: { video_id: string; scene_id: string }[] };
  ranking: Ranking;
  policy: { action: PolicyAction; reasons: string[] };
}

export interface ProviderReport {
  provider: string;
  label: string;
  state: "ready" | "not_configured" | "rate_limited" | "unavailable" | "auth_failed" | "unsupported" | "skipped" | "disabled";
  detail: string;
  count: number;
}

export interface SceneDiscovery {
  scene_id: string;
  searched?: boolean;
  discovered_utc?: string;
  summary?: string;
  recommended?: string | null;
  candidates: VisualCandidate[];
  providers?: ProviderReport[];
  gpu?: { state: string; detail: string };
  autonomy?: { level: string; note: string | null };
  requirement?: { query: string };
  library_hit?: boolean;
  decision?: { status: string } | null;
}

export interface ApprovalTerms {
  source: string | null;
  license: string | null;
  license_url: string | null;
  creator: string | null;
  source_url: string | null;
  restrictions: string[];
  attribution_text: string | null;
  needs_note: boolean;
}

export interface ChoiceResult {
  status: "selected" | "approval_required" | "generation_requested" | "blocked";
  reasons?: string[];
  terms?: ApprovalTerms;
  deferred?: boolean;
  detail?: string;
  stale_stages?: string[];
  near_duplicates?: string[];
}

export interface VisualSource {
  scene_id: string;
  kind: "library" | "stock" | "generated";
  media_kind: string | null;
  provider: string | null;
  creator?: string | null;
  source_url?: string | null;
  license?: string | null;
  attribution_required?: boolean;
  attribution_text?: string | null;
  retrieved_utc?: string | null;
  approval?: { actor: string; utc: string } | null;
  seed?: number | null;
}

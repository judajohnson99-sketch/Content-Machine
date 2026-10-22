import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getAssets } from "../api/assets";
import { libraryFileUrl } from "../api/library";
import {
  chooseSceneAsset,
  getSceneAssets,
  regenerateScene,
  saveSceneAsset,
  searchSceneAssets,
} from "../api/visualAssets";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { ErrorState, InlineSpinner } from "./ui/States";
import type { Tone } from "../lib/statusTokens";
import type {
  ApprovalTerms,
  ChoiceResult,
  ProviderReport,
  VisualCandidate,
} from "../types/visualAssets";
import styles from "./SceneSourcingPanel.module.css";

interface Props {
  videoId: string;
  disabled?: boolean;
}

const PROVIDER_STATE: Record<ProviderReport["state"], { text: string; tone: Tone }> = {
  ready: { text: "ready", tone: "success" },
  not_configured: { text: "not connected", tone: "neutral" },
  rate_limited: { text: "resting — limit reached", tone: "warning" },
  unavailable: { text: "unavailable right now", tone: "warning" },
  auth_failed: { text: "access refused", tone: "danger" },
  unsupported: { text: "no footage", tone: "neutral" },
  skipped: { text: "skipped", tone: "neutral" },
};

const SOURCE_LABEL = (c: VisualCandidate): string =>
  c.source === "library" ? "Your library"
    : c.source === "stock" ? c.provider.charAt(0).toUpperCase() + c.provider.slice(1)
      : c.source === "generated" ? "New image" : "Cloud image";

function licenceBadge(c: VisualCandidate): { text: string; tone: Tone } {
  if (c.policy.action === "blocked") return { text: "Can't be used yet", tone: "danger" };
  if (c.policy.action === "approval_required") {
    return c.rights.eligibility === "review_required"
      ? { text: "Rights need your review", tone: "warning" }
      : { text: "Needs your approval", tone: "warning" };
  }
  return { text: c.source === "generated" ? "Yours" : "Cleared", tone: "success" };
}

function previewSrc(c: VisualCandidate): string | null {
  if (c.source === "library" && c.library) return libraryFileUrl(c.library.asset_id);
  return c.preview_url;
}

function summary(c: VisualCandidate): string {
  const bits: string[] = [c.media_kind === "video" ? "Footage" : "Photo"];
  if (c.width && c.height) bits.push(`${c.width}×${c.height}`);
  if (c.duration_seconds) bits.push(`${Math.round(c.duration_seconds)}s`);
  return bits.join(" · ");
}

// One workflow for a shot's picture: search your library, stock sites and a
// new generation together, see them ranked with the reasons, and choose.
// Choosing external media for the first time asks for your approval; nothing
// is downloaded or generated until you do. Whether the result is good enough
// to publish is still the review gate's call, not this panel's.
export function SceneSourcingPanel({ videoId, disabled }: Props) {
  const queryClient = useQueryClient();
  const [pickedScene, setSceneId] = useState("");
  const [query, setQuery] = useState("");
  const [asking, setAsking] = useState<{ candidate: VisualCandidate; terms: ApprovalTerms } | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<string | null>(null);

  const assetsQuery = useQuery({
    queryKey: ["project-assets", videoId],
    queryFn: () => getAssets(videoId),
  });
  const scenes = assetsQuery.data?.storyboard?.scenes ?? [];
  // Derived rather than synced: until a shot is picked, the first one is shown.
  const sceneId = pickedScene || scenes[0]?.scene_id || "";

  const discovery = useQuery({
    queryKey: ["scene-assets", videoId, sceneId],
    queryFn: () => getSceneAssets(videoId, sceneId),
    enabled: !!sceneId,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["owner-media", videoId] });
    void queryClient.invalidateQueries({ queryKey: ["project-assets", videoId] });
    void queryClient.invalidateQueries({ queryKey: ["project", videoId] });
  };

  const search = useMutation({
    mutationFn: (more: boolean) =>
      searchSceneAssets(videoId, sceneId, { query: query.trim() || undefined, more }),
    onSuccess: (data) => {
      setOutcome(null);
      queryClient.setQueryData(["scene-assets", videoId, sceneId], data);
    },
  });

  const handle = (candidate: VisualCandidate, result: ChoiceResult) => {
    if (result.status === "approval_required" && result.terms) {
      setAsking({ candidate, terms: result.terms });
      setAgreed(false);
      setNote("");
      return;
    }
    setAsking(null);
    if (result.status === "selected") {
      setOutcome(`Chosen for ${sceneId}. Run the Scenes step again to use it in the video.`);
    } else if (result.status === "generation_requested") {
      setOutcome(result.deferred
        ? `A new image for ${sceneId} will be made when the GPU is on. The rest of the production can continue.`
        : `A new image for ${sceneId} is queued. Run the Scenes step to generate it.`);
    } else {
      setOutcome((result.reasons ?? ["That option can't be used."]).join(" "));
    }
    refresh();
    void queryClient.invalidateQueries({ queryKey: ["scene-assets", videoId, sceneId] });
  };

  const choose = useMutation({
    mutationFn: (v: { candidate: VisualCandidate; approve?: boolean; note?: string }) =>
      chooseSceneAsset(videoId, sceneId, {
        candidate_id: v.candidate.candidate_id, approve: v.approve, note: v.note,
      }),
    onSuccess: (result, v) => handle(v.candidate, result),
  });
  const save = useMutation({
    mutationFn: (candidate: VisualCandidate) =>
      saveSceneAsset(videoId, sceneId, { candidate_id: candidate.candidate_id }),
    onSuccess: () => setOutcome("Saved to your library. It will ask for your approval the first time it is used."),
  });
  const regenerate = useMutation({
    mutationFn: () => regenerateScene(videoId, sceneId),
    onSuccess: () => {
      setOutcome(`${sceneId} will be generated again with a new seed on the next Scenes run.`);
      refresh();
      void queryClient.invalidateQueries({ queryKey: ["scene-assets", videoId, sceneId] });
    },
  });

  if (assetsQuery.isLoading) return <InlineSpinner label="Loading shots…" />;
  if (scenes.length === 0) {
    return <p className={styles.providerNote}>Build the storyboard first — shots appear here once it exists.</p>;
  }

  const data = discovery.data;
  const searched = !!data && data.searched !== false && data.candidates.length + (data.providers?.length ?? 0) > 0;
  const error = (search.error || choose.error || save.error || regenerate.error) as Error | null;
  const busy = search.isPending || choose.isPending || save.isPending || regenerate.isPending;

  return (
    <div className={styles.panel}>
      <h3 className={styles.heading}>Find a picture for each shot</h3>
      <div className={styles.head}>
        <label className={styles.sceneSelect}>
          Shot
          <select value={sceneId} onChange={(e) => { setSceneId(e.target.value); setOutcome(null); setAsking(null); }}>
            {scenes.map((s) => (
              <option key={s.scene_id} value={s.scene_id}>
                {s.scene_id}{s.section ? ` · ${s.section}` : ""}
              </option>
            ))}
          </select>
        </label>
        <form
          className={styles.search}
          onSubmit={(e) => { e.preventDefault(); search.mutate(false); }}
        >
          <label className={styles.sceneSelect} style={{ flex: "1 1 auto" }}>
            What should this shot show? (optional)
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Uses the shot's own plan if left empty" />
          </label>
          <Button type="submit" variant="primary" disabled={disabled || busy}>
            {search.isPending ? "Searching…" : "Find options"}
          </Button>
        </form>
      </div>

      {error && <ErrorState compact title="That didn't work" description={error.message} />}
      {outcome && <div className={styles.notice} role="status">{outcome}</div>}

      {data && (data.providers?.length ?? 0) > 0 && (
        <div>
          <div className={styles.providers}>
            {data.providers!.map((p) => (
              <Badge key={p.provider} tone={PROVIDER_STATE[p.state].tone} title={p.detail}>
                {p.label}: {PROVIDER_STATE[p.state].text}
              </Badge>
            ))}
            {data.gpu && (
              <Badge tone={data.gpu.state === "ready" ? "success" : "neutral"} title={data.gpu.detail}>
                New images: {data.gpu.state === "ready" ? "ready" : "waiting for the GPU"}
              </Badge>
            )}
          </div>
          {data.library_hit && (
            <p className={styles.providerNote}>
              A strong match is already in your library, so stock sites were skipped.{" "}
              <Button size="sm" variant="ghost" disabled={disabled || busy} onClick={() => search.mutate(true)}>
                Search stock anyway
              </Button>
            </p>
          )}
        </div>
      )}

      {data?.summary && searched && <p className={styles.summary}>{data.summary}</p>}

      {asking && (
        <div className={styles.approval} role="group" aria-label="Approve first use">
          <h4>Approve using this {asking.candidate.media_kind === "video" ? "footage" : "photo"}?</h4>
          <p className={styles.why}>
            {asking.terms.source} licence: {asking.terms.license}
            {asking.terms.license_url && <> · <a href={asking.terms.license_url} target="_blank" rel="noreferrer">read the terms ↗</a></>}
            {asking.terms.creator && <> · by {asking.terms.creator}</>}
          </p>
          {asking.terms.restrictions.length > 0 && (
            <ul>{asking.terms.restrictions.map((r) => <li key={r}>{r}</li>)}</ul>
          )}
          {asking.terms.attribution_text && (
            <p className={styles.why}>Credit that will be required: {asking.terms.attribution_text}</p>
          )}
          <label>
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
            I've checked these terms and approve using this in my production.
          </label>
          {asking.terms.needs_note && (
            <label className={styles.sceneSelect}>
              This record is incomplete — what did you check?
              <textarea value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
          )}
          <div className={styles.actions}>
            <Button
              variant="primary"
              disabled={!agreed || (asking.terms.needs_note && !note.trim()) || busy}
              onClick={() => choose.mutate({ candidate: asking.candidate, approve: true, note })}
            >
              Approve and use
            </Button>
            <Button variant="ghost" onClick={() => setAsking(null)}>Cancel</Button>
          </div>
        </div>
      )}

      {discovery.isLoading && <InlineSpinner label="Loading options…" />}
      {!discovery.isLoading && !searched && (
        <p className={styles.providerNote}>
          Find options to compare your library, stock photos and footage, and a new image for this shot.
        </p>
      )}

      {data && data.candidates.length > 0 && (
        <ul className={styles.grid}>
          {data.candidates.map((c) => (
            <CandidateCard
              key={c.candidate_id}
              candidate={c}
              disabled={disabled || busy}
              onChoose={() => choose.mutate({ candidate: c })}
              onSave={() => save.mutate(c)}
            />
          ))}
        </ul>
      )}

      {searched && (
        <div className={styles.actions}>
          <Button variant="ghost" disabled={disabled || busy} onClick={() => regenerate.mutate()}>
            Generate this shot again
          </Button>
        </div>
      )}
    </div>
  );
}

function CandidateCard({ candidate: c, disabled, onChoose, onSave }: {
  candidate: VisualCandidate; disabled?: boolean; onChoose: () => void; onSave: () => void;
}) {
  const licence = licenceBadge(c);
  const src = previewSrc(c);
  const confidenceTone: Tone = c.ranking.confidence === "high" ? "success" : c.ranking.confidence === "medium" ? "info" : "neutral";
  const generating = c.source === "generated";
  return (
    <li className={`${styles.card} ${c.ranking.recommended ? styles.recommended : ""} ${c.policy.action === "blocked" && !generating ? styles.blocked : ""}`}>
      <div className={styles.preview}>
        {src && c.source === "library" && c.media_kind === "video" ? (
          <video src={src} muted controls preload="metadata" aria-label={`Preview of ${c.title || c.candidate_id}`} />
        ) : src ? (
          <img src={src} alt={c.title || "Candidate preview"} loading="lazy" />
        ) : (
          <span className={styles.previewEmpty}>
            {generating ? c.state_detail ?? "Not generated yet" : "No preview"}
          </span>
        )}
        {c.ranking.recommended && <span className={styles.pick}><Badge tone="accent">Recommended</Badge></span>}
      </div>
      <div className={styles.body}>
        <h4 className={styles.title}>{c.title || (generating ? "New image for this shot" : "Untitled")}</h4>
        <p className={styles.meta}>
          {SOURCE_LABEL(c)} · {summary(c)}
          {c.creator?.name && <> · {c.creator.name}</>}
        </p>
        <div className={styles.badges}>
          <Badge tone={licence.tone}>{licence.text}</Badge>
          <Badge tone="info" title="How well this fits the shot, from 0 to 100">Match {Math.round(c.ranking.score * 100)}</Badge>
          <Badge tone={confidenceTone} title={c.ranking.confidence_reasons.join("; ")}>
            {c.ranking.confidence} confidence
          </Badge>
          {(c.near_duplicates?.length ?? 0) > 0 && <Badge tone="neutral">similar to {c.near_duplicates!.length} more</Badge>}
        </div>
        <p className={styles.why}>{c.ranking.summary}</p>
        {c.policy.action !== "auto" && c.policy.reasons.length > 0 && (
          <p className={styles.why}>{c.policy.reasons.join(" ")}</p>
        )}
        <details className={styles.details}>
          <summary>Why this score</summary>
          <ul className={styles.factors}>
            {c.ranking.factors.filter((f) => f.applied).map((f) => (
              <li key={f.id} className={styles.factor}>
                <span>{f.label}: {f.detail}</span>
                <span>{Math.round((f.score ?? 0) * 100)}</span>
              </li>
            ))}
          </ul>
          {(c.ranking.limitations.length > 0 || c.ranking.advisory.length > 0) && (
            <ul className={styles.limits}>
              {[...c.ranking.limitations, ...c.ranking.advisory].map((l) => <li key={l}>{l}</li>)}
            </ul>
          )}
          {c.ranking.alternatives.length > 0 && (
            <ul className={styles.limits}>
              {c.ranking.alternatives.map((a) => <li key={a.candidate_id}>Alternative: {a.why}</li>)}
            </ul>
          )}
        </details>
        <div className={styles.actions}>
          <Button
            size="sm"
            variant={c.ranking.recommended ? "primary" : "secondary"}
            disabled={disabled || (c.policy.action === "blocked" && !generating) || c.source === "cloud"}
            onClick={onChoose}
          >
            {generating ? (c.state === "deferred" ? "Generate when the GPU is on" : "Generate for this shot") : "Use for this shot"}
          </Button>
          {c.source === "stock" && (
            <Button size="sm" variant="ghost" disabled={disabled} onClick={onSave}>Save to library</Button>
          )}
          {c.source === "stock" && c.source_url && (
            <a href={c.source_url} target="_blank" rel="noreferrer">Original ↗</a>
          )}
        </div>
      </div>
    </li>
  );
}

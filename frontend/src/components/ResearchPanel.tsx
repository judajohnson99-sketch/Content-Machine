import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getResearchBrief, getResearchFindings, saveResearchBrief } from "../api/research";
import { ApiError } from "../api/client";
import type { Finding, FindingKind, SeedReference } from "../types/research";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { StageActionButton } from "./StageActionButton";
import { InlineSpinner } from "./ui/States";
import type { Tone } from "../lib/statusTokens";
import styles from "./ResearchPanel.module.css";

interface Props {
  videoId: string;
  disabled?: boolean;
  disabledReason?: string;
}

const KIND_TONE: Record<FindingKind, Tone> = {
  observation: "info",
  interpretation: "warning",
  hypothesis: "neutral",
};

const KIND_LABEL: Record<FindingKind, string> = {
  observation: "sourced observation",
  interpretation: "interpretation",
  hypothesis: "hypothesis",
};

// The brief is a plain draft edited locally until "Save brief" - it does
// not autosave, so a half-typed thought never overwrites a good brief.
interface DraftState {
  niche: string;
  creative_intent: string;
  likes: string;
  dislikes: string;
  seed_references: SeedReference[];
  notes: string;
}

const EMPTY_DRAFT: DraftState = {
  niche: "", creative_intent: "", likes: "", dislikes: "", seed_references: [], notes: "",
};

export function ResearchPanel({ videoId, disabled, disabledReason }: Props) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<DraftState>(EMPTY_DRAFT);
  const [dirty, setDirty] = useState(false);

  const briefQuery = useQuery({
    queryKey: ["research-brief", videoId],
    queryFn: () => getResearchBrief(videoId),
  });

  const findingsQuery = useQuery({
    queryKey: ["research-findings", videoId],
    queryFn: () => getResearchFindings(videoId),
    refetchInterval: 15_000,
  });

  // Load the saved brief into the draft once, and whenever a fresh save
  // lands from elsewhere - but never while the operator has unsaved edits.
  useEffect(() => {
    if (dirty) return;
    const b = briefQuery.data;
    setDraft(b ? {
      niche: b.niche, creative_intent: b.creative_intent ?? "",
      likes: b.likes.join(", "), dislikes: b.dislikes.join(", "),
      seed_references: b.seed_references, notes: b.notes ?? "",
    } : EMPTY_DRAFT);
  }, [briefQuery.data, dirty]);

  const save = useMutation({
    mutationFn: () => saveResearchBrief(videoId, {
      niche: draft.niche.trim(),
      creative_intent: draft.creative_intent.trim() || null,
      likes: splitList(draft.likes),
      dislikes: splitList(draft.dislikes),
      seed_references: draft.seed_references.filter((r) => r.value.trim()),
      notes: draft.notes.trim() || null,
    }),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["research-brief", videoId] });
    },
  });

  const set = <K extends keyof DraftState>(key: K, value: DraftState[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setDirty(true);
  };

  const addSeed = () => set("seed_references", [...draft.seed_references, { type: "channel", value: "" }]);
  const removeSeed = (i: number) => set("seed_references", draft.seed_references.filter((_, idx) => idx !== i));
  const updateSeed = (i: number, patch: Partial<SeedReference>) =>
    set("seed_references", draft.seed_references.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  const findings = findingsQuery.data;
  const byTopic = groupByTopic(findings?.findings ?? []);

  return (
    <div className={styles.panel} data-testid="research-panel">
      {briefQuery.isLoading ? (
        <InlineSpinner label="Loading research brief…" />
      ) : (
        <>
          <label className={styles.field}>
            <span className={styles.label}>Niche</span>
            <input
              className={styles.input}
              value={draft.niche}
              onChange={(e) => set("niche", e.target.value)}
              placeholder="e.g. sleep ambience, brown noise"
            />
          </label>

          <label className={styles.field}>
            <span className={styles.label}>Creative intent</span>
            <textarea
              className={styles.textarea}
              value={draft.creative_intent}
              onChange={(e) => set("creative_intent", e.target.value)}
              placeholder="What is this video trying to feel like?"
            />
          </label>

          <label className={styles.field}>
            <span className={styles.label}>Likes (comma-separated)</span>
            <input className={styles.input} value={draft.likes}
                  onChange={(e) => set("likes", e.target.value)} />
          </label>

          <label className={styles.field}>
            <span className={styles.label}>Dislikes (comma-separated)</span>
            <input className={styles.input} value={draft.dislikes}
                  onChange={(e) => set("dislikes", e.target.value)} />
          </label>

          <div className={styles.field}>
            <span className={styles.label}>Seed channels/videos (optional)</span>
            {draft.seed_references.map((ref, i) => (
              <div className={styles.seedRow} key={i}>
                <select
                  className={styles.select}
                  value={ref.type}
                  onChange={(e) => updateSeed(i, { type: e.target.value as SeedReference["type"] })}
                >
                  <option value="channel">Channel</option>
                  <option value="video">Video</option>
                </select>
                <input
                  className={styles.input}
                  value={ref.value}
                  onChange={(e) => updateSeed(i, { value: e.target.value })}
                  placeholder="URL or handle"
                />
                <button type="button" className={styles.removeButton} onClick={() => removeSeed(i)} aria-label="Remove reference">
                  ×
                </button>
              </div>
            ))}
            <button type="button" className={styles.addButton} onClick={addSeed}>+ Add a seed reference</button>
          </div>

          <div className={styles.saveRow}>
            <Button
              variant="primary" size="sm"
              onClick={() => save.mutate()}
              disabled={disabled || save.isPending || !draft.niche.trim()}
            >
              {save.isPending ? "Saving…" : "Save brief"}
            </Button>
            {briefQuery.data && (
              <StageActionButton
                videoId={videoId}
                stage="research"
                label="Run / refresh research"
                params={{ force: true }}
                disabled={disabled}
                disabledReason={disabledReason}
              />
            )}
            {save.isSuccess && !dirty && <span className={styles.saveStatus}>Saved.</span>}
            {save.isError && (
              <span className={styles.saveError}>
                {save.error instanceof ApiError ? save.error.message : "Save failed."}
              </span>
            )}
          </div>
        </>
      )}

      <hr className={styles.divider} />

      <div className={styles.findingsHead}>
        <span className={styles.label}>Research findings</span>
        {findings && (
          <span className={styles.findingsMeta}>
            {findings.findings.length} finding(s) via {findings.provider}
          </span>
        )}
      </div>

      {findingsQuery.isLoading && <InlineSpinner label="Loading findings…" />}
      {!findingsQuery.isLoading && !findings && (
        <p className={styles.muted}>
          No findings yet. Save a brief with a niche (and, optionally, seed references),
          then run research from the Pipeline card - or above, once a brief exists.
        </p>
      )}
      {Object.entries(byTopic).map(([topic, items]) => (
        <div className={styles.topicGroup} key={topic}>
          <span className={styles.topicLabel}>{topic}</span>
          {items.map((f) => (
            <div className={styles.finding} key={f.finding_id}>
              <div className={styles.findingHead}>
                <Badge tone={KIND_TONE[f.kind]}>{KIND_LABEL[f.kind]}</Badge>
              </div>
              <span>{f.statement}</span>
              {f.source_url && (
                <span className={styles.findingSource}>
                  <a href={f.source_url} target="_blank" rel="noreferrer">
                    {f.source_title || f.source_url}
                  </a>
                </span>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function splitList(text: string): string[] {
  return text.split(",").map((s) => s.trim()).filter(Boolean);
}

function groupByTopic(findings: Finding[]): Record<string, Finding[]> {
  const out: Record<string, Finding[]> = {};
  for (const f of findings) {
    (out[f.topic] ??= []).push(f);
  }
  return out;
}

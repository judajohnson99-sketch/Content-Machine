import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { createProject, listConcepts } from "../../api/concepts";
import { triggerStage } from "../../api/pipeline";
import { ApiError } from "../../api/client";
import { newClientRequestId } from "../../lib/clientRequestId";
import { paletteColor, paletteGradient } from "../../lib/palette";
import { gpuStateLabel, statusTone, type Tone } from "../../lib/statusTokens";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { PageHeader } from "../../components/ui/PageHeader";
import { ErrorState, SkeletonRows } from "../../components/ui/States";
import { GpuIcon, SearchIcon, SparkIcon, WaveIcon } from "../../components/ui/icons";
import { conceptKind, type ConceptCatalog, type ConceptKind, type ConceptSummary, type Readiness } from "../../types/concepts";
import { GoalComposer } from "./GoalComposer";
import styles from "./NewProjectPage.module.css";

const READINESS_TONE: Record<Readiness, Tone> = {
  ok: "success",
  partial: "warning",
  blocked: "danger",
  "n/a": "neutral",
};

const KIND_LABEL: Record<ConceptKind, string> = {
  reference: "Reference catalogue",
  experiment: "Experiments",
  technical: "Technical",
};

type ImageMode = "auto" | "scenes" | "plates";

// A project id is a directory name and a URL segment; suggest one the
// domain layer will accept (scripts.experiment.VIDEO_ID_RE) and let the
// operator edit it.
function suggestVideoId(conceptId: string): string {
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  return `${conceptId.replace(/^ref-/, "")}-${stamp}`.toLowerCase().replace(/[^a-z0-9-]/g, "-").slice(0, 64);
}

function readinessLabel(c: ConceptSummary): { tone: Tone; label: string } {
  if (c.readiness.runnable_now) return { tone: "success", label: "runnable now" };
  if (c.readiness.waits_for_gpu) return { tone: "warning", label: "waits for GPU" };
  return { tone: "danger", label: "needs setup" };
}

export function NewProjectPage() {
  const navigate = useNavigate();
  const catalog = useQuery({ queryKey: ["concepts"], queryFn: listConcepts });

  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<ConceptKind | null>(null);
  const [niche, setNiche] = useState<string | null>(null);
  const [runnableOnly, setRunnableOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [videoId, setVideoId] = useState("");
  const [minutes, setMinutes] = useState<string>("");
  const [preview, setPreview] = useState(false);
  const [imageMode, setImageMode] = useState<ImageMode>("auto");
  const [startProduce, setStartProduce] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const concepts = catalog.data?.concepts;
  const kindsPresent = useMemo(() => {
    const present = new Set((concepts ?? []).map(conceptKind));
    return (["reference", "experiment", "technical"] as ConceptKind[]).filter((k) => present.has(k));
  }, [concepts]);
  // Lead with the reference catalogue when it exists; technical concepts
  // are never the default view.
  const activeKind: ConceptKind | null = kind ?? kindsPresent.find((k) => k !== "technical") ?? kindsPresent[0] ?? null;

  useEffect(() => {
    setNiche(null);
  }, [activeKind]);

  const inKind = useMemo(() => (concepts ?? []).filter((c) => conceptKind(c) === activeKind), [concepts, activeKind]);
  const niches = useMemo(() => Array.from(new Set(inKind.map((c) => c.niche))).sort(), [inKind]);
  const selected = (concepts ?? []).find((c) => c.id === selectedId) ?? null;

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return inKind.filter(
      (c) =>
        (!niche || c.niche === niche) &&
        (!runnableOnly || c.readiness.runnable_now) &&
        (!q ||
          c.id.includes(q) ||
          (c.title_pattern ?? "").toLowerCase().includes(q) ||
          (c.tagline ?? "").toLowerCase().includes(q) ||
          c.content_format.toLowerCase().includes(q)),
    );
  }, [inKind, niche, runnableOnly, query]);

  function choose(concept: ConceptSummary) {
    setSelectedId(concept.id);
    setVideoId(suggestVideoId(concept.id));
    setMinutes(String(concept.video_length_minutes));
    setPreview(false);
    setError(null);
  }

  const mutation = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error("Choose a concept first.");
      const duration = preview
        ? selected.preview_seconds ?? 60
        : minutes.trim()
          ? Number(minutes) * 60
          : null;
      const project = await createProject({ video_id: videoId.trim(), concept_id: selected.id, duration });
      if (startProduce) {
        const params = imageMode === "auto" ? {} : { scenes: imageMode === "scenes" };
        await triggerStage(project.video_id, "produce", newClientRequestId(), params);
      }
      return project;
    },
    onSuccess: (project) => navigate(`/projects/${project.video_id}`),
    onError: (e: unknown) => {
      if (e instanceof ApiError && e.status === 409) {
        setError(`A project called "${videoId}" already exists — pick another id.`);
      } else {
        setError(e instanceof Error ? e.message : "Request failed.");
      }
    },
  });

  const idValid = /^[a-z0-9][a-z0-9-]{2,63}$/.test(videoId.trim());
  const minutesValid = preview || !minutes.trim() || Number(minutes) > 0;
  const caps = catalog.data?.capabilities;

  return (
    <div>
      <PageHeader
        backTo={{ to: "/", label: "Dashboard" }}
        eyebrow="New production"
        title="Start a production"
        description="Describe what you want and the studio works out the rest - concept, research brief, imagery, sound and edit. A saved direction from the catalogue below is the other way in."
      />

      <GoalComposer />

      <h2 className={styles.catalogueHeading}>Or start from a saved direction</h2>

      {catalog.isLoading && <SkeletonRows count={6} />}
      {catalog.isError && (
        <ErrorState
          title="Failed to load the concept catalogue"
          where="GET /api/v1/concepts/"
          description={(catalog.error as Error).message}
          hint="The catalogue is read from experiments/concepts.json on the server; if it fails to parse, `./content-machine experiment validate` says why."
          action={<Button size="sm" onClick={() => catalog.refetch()}>Retry</Button>}
        />
      )}

      {catalog.data && caps && (
        <div className={styles.layout}>
          <div className={styles.catalogue}>
            <CapabilityStrip caps={caps} />

            <div className={styles.toolbar}>
              <div className={styles.segmented} role="tablist" aria-label="Concept kind">
                {kindsPresent.map((k) => (
                  <button
                    key={k}
                    type="button"
                    role="tab"
                    aria-selected={activeKind === k}
                    className={`${styles.segment} ${activeKind === k ? styles.segmentActive : ""}`}
                    onClick={() => setKind(k)}
                  >
                    {KIND_LABEL[k]}
                  </button>
                ))}
              </div>
              <div className={styles.searchWrap}>
                <SearchIcon width={15} height={15} />
                <input
                  className={styles.search}
                  type="search"
                  placeholder="Filter concepts…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  aria-label="Filter concepts"
                />
              </div>
            </div>

            <div className={styles.filters}>
              <button
                type="button"
                className={`${styles.chip} ${runnableOnly ? styles.chipActive : ""}`}
                onClick={() => setRunnableOnly((v) => !v)}
                aria-pressed={runnableOnly}
              >
                Runnable here
              </button>
              {niches.length > 1 &&
                niches.map((n) => (
                  <button
                    key={n}
                    type="button"
                    className={`${styles.chip} ${niche === n ? styles.chipActive : ""}`}
                    onClick={() => setNiche(niche === n ? null : n)}
                    aria-pressed={niche === n}
                  >
                    {n.replace(/_/g, " ")}
                  </button>
                ))}
            </div>

            {activeKind === "technical" && (
              <p className={styles.kindNote}>
                Technical concepts exercise the pipeline (QC luminance floor, long renders). They are regression material, not what the channel publishes.
              </p>
            )}

            <ul className={`${styles.list} ${activeKind === "reference" ? styles.gridList : ""}`} aria-label="Concepts">
              {visible.map((c) =>
                activeKind === "reference" ? (
                  <li key={c.id}>
                    <ReferenceCard concept={c} selected={selectedId === c.id} onChoose={() => choose(c)} />
                  </li>
                ) : (
                  <li key={c.id}>
                    <ConceptRow concept={c} selected={selectedId === c.id} onChoose={() => choose(c)} />
                  </li>
                ),
              )}
              {visible.length === 0 && <li className={styles.hint}>No concepts match.</li>}
            </ul>
          </div>

          <aside className={styles.inspector}>
            <Card className={styles.inspectorCard}>
              {!selected && (
                <div className={styles.inspectorEmpty}>
                  <span className={styles.stepBadge}>1</span>
                  <p className={styles.inspectorTitle}>Select a direction</p>
                  <p className={styles.hint}>The catalogue on the left holds curated reference concepts, the experiment batch, and technical regression material.</p>
                </div>
              )}
              {selected && (
                <form
                  className={styles.form}
                  onSubmit={(e) => {
                    e.preventDefault();
                    setError(null);
                    mutation.mutate();
                  }}
                >
                  <div className={styles.step}>
                    <div className={styles.stepHead}>
                      <span className={styles.stepBadge}>1</span>
                      <span className={styles.stepTitle}>Direction</span>
                      <Badge tone={readinessLabel(selected).tone}>{readinessLabel(selected).label}</Badge>
                    </div>
                    <div className={styles.art} style={{ background: paletteGradient(selected.visual_direction?.palette, selected.id) }} aria-hidden="true" />
                    <h2 className={styles.selectedTitle}>{selected.title_pattern ?? selected.id}</h2>
                    {selected.tagline && <p className={styles.tagline}>{selected.tagline}</p>}
                    {selected.creative_intent && <p className={styles.intent}>{selected.creative_intent}</p>}

                    <dl className={styles.facts}>
                      <div>
                        <dt><SparkIcon width={14} height={14} /> Visual direction</dt>
                        <dd>
                          {selected.visual_direction?.palette && (
                            <span className={styles.swatches} aria-label="Palette">
                              {selected.visual_direction.palette.map((w) => (
                                <span key={w} className={styles.swatch} title={w}>
                                  <span className={styles.swatchDot} style={{ background: paletteColor(w) }} />
                                  {w}
                                </span>
                              ))}
                            </span>
                          )}
                          <span className={styles.factText}>{selected.visual_direction?.atmosphere ?? selected.visual_concept}</span>
                          {selected.visual_direction?.motifs && (
                            <span className={styles.factSub}>Motifs: {selected.visual_direction.motifs.join(", ")}</span>
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt><WaveIcon width={14} height={14} /> Audio direction</dt>
                        <dd>
                          <span className={styles.factText}>{selected.audio_concept}</span>
                          <span className={styles.factSub}>
                            {selected.narration === "narrated" ? "Narrated (local Piper voice)" : "No narration"} ·{" "}
                            {selected.audio_requirement.replace(/_/g, " ")}
                          </span>
                        </dd>
                      </div>
                    </dl>

                    <div className={styles.readinessRow}>
                      <Badge tone={READINESS_TONE[selected.readiness.images]}>
                        {selected.procedural_visuals_acceptable ? "procedural ok" : "depicted required"}
                      </Badge>
                      <Badge tone={READINESS_TONE[selected.readiness.audio]}>audio {selected.readiness.audio}</Badge>
                      <Badge tone={READINESS_TONE[selected.readiness.research]}>
                        {selected.requires_subject_research ? "source-backed research" : "no research needed"}
                      </Badge>
                    </div>
                    {selected.readiness.notes.length > 0 && (
                      <ul className={styles.notes}>
                        {selected.readiness.notes.map((n) => (
                          <li key={n}>{n}</li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div className={styles.step}>
                    <div className={styles.stepHead}>
                      <span className={styles.stepBadge}>2</span>
                      <span className={styles.stepTitle}>Essentials</span>
                    </div>

                    <label className={styles.field}>
                      <span className={styles.label}>Project id</span>
                      <input
                        className={styles.input}
                        value={videoId}
                        onChange={(e) => setVideoId(e.target.value)}
                        aria-invalid={!idValid}
                        spellCheck={false}
                      />
                      <span className={styles.hint}>3–64 lowercase letters, digits or hyphens. Becomes the folder and the URL.</span>
                    </label>

                    <div className={styles.lengthRow}>
                      <label className={styles.field}>
                        <span className={styles.label}>Duration (minutes)</span>
                        <input
                          className={styles.input}
                          type="number"
                          min={0.1}
                          step="any"
                          value={minutes}
                          onChange={(e) => setMinutes(e.target.value)}
                          aria-invalid={!minutesValid}
                          disabled={preview}
                        />
                        <span className={styles.hint}>Blank keeps the concept template's default.</span>
                      </label>
                      <label className={`${styles.toggle} ${preview ? styles.toggleOn : ""}`}>
                        <input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} />
                        <span>
                          <span className={styles.toggleTitle}>Quality preview</span>
                          <span className={styles.toggleSub}>{selected.preview_seconds ?? 60} s render to judge the look before a full-length run</span>
                        </span>
                      </label>
                    </div>

                    <label className={styles.field}>
                      <span className={styles.label}>Image mode</span>
                      <select className={styles.select} value={imageMode} onChange={(e) => setImageMode(e.target.value as ImageMode)}>
                        <option value="auto">Automatic (scenes for narrated content, plates otherwise)</option>
                        <option value="scenes">Storyboard scenes</option>
                        <option value="plates">Single plate set</option>
                      </select>
                    </label>

                    <label className={styles.checkbox}>
                      <input type="checkbox" checked={startProduce} onChange={(e) => setStartProduce(e.target.checked)} />
                      Start Produce immediately
                    </label>
                  </div>

                  <div className={styles.step}>
                    <div className={styles.stepHead}>
                      <span className={styles.stepBadge}>3</span>
                      <span className={styles.stepTitle}>Create</span>
                    </div>
                    {error && (
                      <p role="alert" className={styles.error}>
                        {error}
                      </p>
                    )}
                    <div className={styles.actions}>
                      <Button type="submit" variant="primary" size="lg" disabled={!idValid || !minutesValid || mutation.isPending}>
                        {mutation.isPending ? "Creating…" : startProduce ? "Create & Produce" : "Create project"}
                      </Button>
                      {selected.readiness.waits_for_gpu && (
                        <span className={styles.hint}>Depicted imagery will queue for the GPU worker; production pauses at the image stage until it renders.</span>
                      )}
                      {!selected.readiness.runnable_now && !selected.readiness.waits_for_gpu && (
                        <span className={styles.hint}>This concept needs setup before it can finish here; you can still create it.</span>
                      )}
                    </div>
                  </div>
                </form>
              )}
            </Card>
          </aside>
        </div>
      )}
    </div>
  );
}

function CapabilityStrip({ caps }: { caps: ConceptCatalog["capabilities"] }) {
  const depicted = caps.depicted_imagery;
  const depictedTone = depicted ? statusTone(depicted.state) : caps.depicted_image_providers.length ? "success" : "neutral";
  return (
    <div className={styles.capabilities} aria-label="Host capabilities">
      <span className={styles.capability} title={depicted?.detail ?? ""}>
        <span className={`${styles.capIcon} ${styles[`tone_${depictedTone}`]}`}><GpuIcon width={14} height={14} /></span>
        <span>
          <span className={styles.capName}>Depicted imagery</span>
          <span className={styles.capValue}>
            {depicted ? gpuStateLabel(depicted.state) : caps.depicted_image_providers.length ? caps.depicted_image_providers.join(", ") : "no provider"}
          </span>
        </span>
      </span>
      <span className={styles.capability}>
        <span className={`${styles.capIcon} ${styles.tone_success}`}><SparkIcon width={14} height={14} /></span>
        <span>
          <span className={styles.capName}>Procedural plates</span>
          <span className={styles.capValue}>always available</span>
        </span>
      </span>
      <span className={styles.capability}>
        <span className={`${styles.capIcon} ${styles.tone_success}`}><WaveIcon width={14} height={14} /></span>
        <span>
          <span className={styles.capName}>Narration</span>
          <span className={styles.capValue}>piper (local)</span>
        </span>
      </span>
      <span className={styles.capability}>
        <span className={`${styles.capIcon} ${styles[`tone_${caps.search_available ? "success" : "neutral"}`]}`}><SearchIcon width={14} height={14} /></span>
        <span>
          <span className={styles.capName}>Research</span>
          <span className={styles.capValue}>{caps.search_available ? caps.search_provider : "no search provider"}</span>
        </span>
      </span>
    </div>
  );
}

function ReferenceCard({ concept, selected, onChoose }: { concept: ConceptSummary; selected: boolean; onChoose: () => void }) {
  const r = readinessLabel(concept);
  return (
    <button
      type="button"
      className={`${styles.refCard} ${selected ? styles.refCardSelected : ""}`}
      onClick={onChoose}
      aria-pressed={selected}
    >
      <span className={styles.refArt} style={{ background: paletteGradient(concept.visual_direction?.palette, concept.id) }} aria-hidden="true">
        <span className={styles.refArtGlow} />
      </span>
      <span className={styles.refBody}>
        <span className={styles.refTitle}>{concept.title_pattern ?? concept.id}</span>
        {concept.tagline && <span className={styles.refTagline}>{concept.tagline}</span>}
        <span className={styles.refMeta}>
          <Badge tone={r.tone}>{r.label}</Badge>
          <span className={styles.refFacts}>
            {concept.narration === "narrated" ? "narrated" : "no narration"} · {concept.video_length_minutes} min
          </span>
        </span>
      </span>
    </button>
  );
}

function ConceptRow({ concept, selected, onChoose }: { concept: ConceptSummary; selected: boolean; onChoose: () => void }) {
  const r = readinessLabel(concept);
  return (
    <button
      type="button"
      className={`${styles.concept} ${selected ? styles.conceptSelected : ""}`}
      onClick={onChoose}
      aria-pressed={selected}
    >
      <span className={styles.conceptSwatch} style={{ background: paletteGradient(concept.visual_direction?.palette, concept.id) }} aria-hidden="true" />
      <span className={styles.conceptMain}>
        <span className={styles.conceptTitle}>{concept.title_pattern ?? concept.id}</span>
        <span className={styles.conceptMeta}>
          {concept.niche.replace(/_/g, " ")} · {concept.video_length_minutes} min · {concept.narration === "narrated" ? "narrated" : "no narration"}
        </span>
        <span className={styles.conceptFormat}>{concept.content_format}</span>
      </span>
      <span className={styles.readiness}>
        <Badge tone={r.tone}>{r.label}</Badge>
      </span>
    </button>
  );
}

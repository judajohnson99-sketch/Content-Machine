import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  annotateAsset,
  getOwnerMedia,
  libraryFileUrl,
  listLibrary,
  scanLibrary,
  setOwnerMedia,
  suggestMedia,
} from "../api/library";
import { fileUrl } from "../api/assets";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { StageActionButton } from "./StageActionButton";
import { MediaDialog } from "./MediaDialog";
import { EmptyState, ErrorState, InlineSpinner } from "./ui/States";
import { formatDateTime } from "../lib/format";
import {
  ROLE_HINT,
  ROLE_LABEL,
  ROLE_ORDER,
  type LibraryAsset,
  type MediaKind,
  type OwnerMediaEntry,
  type OwnerMediaRole,
  type OwnerVisualsMode,
} from "../types/library";
import styles from "./OwnMediaPanel.module.css";

interface Props {
  videoId: string;
  disabled?: boolean;
  disabledReason?: string;
}

const ROLE_KIND: Record<OwnerMediaRole, MediaKind[]> = {
  visuals: ["image", "video"],
  music: ["audio"],
  ambience: ["audio"],
  sfx: ["audio"],
};

const STAGE_LABEL: Record<string, string> = {
  scenes: "Scene images", visuals: "Images", audio: "Audio",
};

function duration(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const whole = Math.round(seconds);
  if (whole < 60) return `${whole}s`;
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`;
}

function assetSummary(asset: LibraryAsset): string {
  const bits: string[] = [];
  if (asset.technical.width && asset.technical.height) {
    bits.push(`${asset.technical.width}×${asset.technical.height}`);
  }
  const length = duration(asset.technical.duration_seconds);
  if (length && asset.kind !== "image") bits.push(length);
  if (asset.technical.bytes) bits.push(`${(asset.technical.bytes / 1_048_576).toFixed(1)} MB`);
  return bits.join(" · ");
}

// Use your own media in a production: browse what the library holds, hear or
// see it before choosing, and assign it to the visuals or to one of the
// audio layers. Choosing is all this does - the next run of the affected
// stage uses it, and whether the result is good enough to publish stays a
// human judgement recorded in the review gate.
export function OwnMediaPanel({ videoId, disabled, disabledReason }: Props) {
  const queryClient = useQueryClient();
  const [picking, setPicking] = useState<OwnerMediaRole | null>(null);

  const selectionQuery = useQuery({
    queryKey: ["owner-media", videoId],
    queryFn: () => getOwnerMedia(videoId),
  });
  const modeMutation = useMutation({
    mutationFn: (mode: OwnerVisualsMode) => setOwnerMedia(videoId, { visuals_mode: mode }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["owner-media", videoId] });
      queryClient.invalidateQueries({ queryKey: ["project-status", videoId] });
    },
  });

  if (selectionQuery.isLoading) return <InlineSpinner label="Loading your media…" />;
  if (selectionQuery.isError) {
    return (
      <ErrorState
        compact
        title="Could not load this production's own media"
        where="GET /api/v1/projects/{id}/owner-media/"
        detail={(selectionQuery.error as Error).message}
      />
    );
  }

  const selection = selectionQuery.data!;
  const staleStages = selection.stale_stages ?? [];

  return (
    <div className={styles.panel}>
      {selection.problems.length > 0 && (
        <div className={styles.alert} role="alert">
          <strong className={styles.alertTitle}>
            {selection.problems.length === 1 ? "A selected file has changed" : "Selected files have changed"}
          </strong>
          <ul className={styles.alertList}>
            {selection.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
          <p className={styles.alertHint}>
            Review is blocked until the media is the same as what was chosen — re-select it, or restore the file.
          </p>
        </div>
      )}

      {staleStages.length > 0 && (
        <div className={styles.stale}>
          <p className={styles.staleText}>
            The current render does not use this media yet —{" "}
            {staleStages.map((s) => (STAGE_LABEL[s] ?? s).toLowerCase()).join(" and ")} still
            need to run.
          </p>
          <div className={styles.staleActions}>
            {staleStages.map((stage) => (
              <StageActionButton
                key={stage}
                videoId={videoId}
                stage={stage as "scenes" | "visuals" | "audio"}
                label={`Re-run ${(STAGE_LABEL[stage] ?? stage).toLowerCase()}`}
                disabled={disabled}
                disabledReason={disabledReason}
              />
            ))}
          </div>
        </div>
      )}

      {(selection.roles.visuals?.entries.length ?? 0) > 0 && (
        <label className={styles.field}>
          <span>How your visuals are used</span>
          <select
            aria-label="How your visuals are used"
            value={selection.visuals_mode ?? "all"}
            disabled={disabled || modeMutation.isPending}
            onChange={(e) => modeMutation.mutate(e.target.value as OwnerVisualsMode)}
          >
            <option value="all">Every shot uses my visuals</option>
            <option value="mixed">Mix them in among generated shots</option>
          </select>
        </label>
      )}

      <div className={styles.roles}>
        {ROLE_ORDER.map((role) => (
          <RoleRow
            key={role}
            role={role}
            entries={selection.roles[role]?.entries ?? []}
            open={picking === role}
            disabled={disabled}
            onPick={() => setPicking(picking === role ? null : role)}
            videoId={videoId}
            onSaved={() => {
              setPicking(null);
              queryClient.invalidateQueries({ queryKey: ["owner-media", videoId] });
              queryClient.invalidateQueries({ queryKey: ["project-assets", videoId] });
              queryClient.invalidateQueries({ queryKey: ["project-status", videoId] });
            }}
          />
        ))}
      </div>
    </div>
  );
}

function RoleRow({
  role,
  entries,
  open,
  disabled,
  videoId,
  onPick,
  onSaved,
}: {
  role: OwnerMediaRole;
  entries: OwnerMediaEntry[];
  open: boolean;
  disabled?: boolean;
  videoId: string;
  onPick: () => void;
  onSaved: () => void;
}) {
  return (
    <section className={styles.role} data-role={role}>
      <div className={styles.roleHead}>
        <div>
          <h4 className={styles.roleTitle}>{ROLE_LABEL[role]}</h4>
          <p className={styles.roleHint}>
            {entries.length > 0 ? ROLE_HINT[role] : `Generated ${role === "visuals" ? "imagery" : "audio"} is used.`}
          </p>
        </div>
        <Button size="sm" onClick={onPick} aria-expanded={open} disabled={disabled}>
          {open ? "Close" : entries.length > 0 ? "Change" : "Choose from library"}
        </Button>
      </div>

      {entries.length > 0 && (
        <ul className={styles.chosen}>
          {entries.map((entry, index) => (
            <li key={entry.asset_id} className={styles.chosenItem}>
              {entry.kind !== "audio" && <ChosenPreview videoId={videoId} entry={entry} />}
              <div className={styles.chosenMeta}>
                <span className={styles.chosenName}>
                  {role === "visuals" && <span className={styles.order}>{index + 1}</span>}
                  {entry.description || entry.asset_id.slice(0, 12)}
                </span>
                <span className={styles.chosenSub}>
                  {entry.kind}
                  {entry.selected_utc ? ` · chosen ${formatDateTime(entry.selected_utc)}` : ""}
                </span>
              </div>
              {entry.kind === "audio" && (
                <ChosenPreview videoId={videoId} entry={entry} audio />
              )}
            </li>
          ))}
        </ul>
      )}

      {open && (
        <LibraryPicker
          role={role}
          videoId={videoId}
          initial={entries.map((e) => e.asset_id)}
          onSaved={onSaved}
        />
      )}
    </section>
  );
}

function ChosenPreview({ videoId, entry, audio = false }: { videoId: string; entry: OwnerMediaEntry; audio?: boolean }) {
  const [error, setError] = useState(false);
  const previewUrl = entry.file?.path ? fileUrl(videoId, entry.file.path) : libraryFileUrl(entry.asset_id);
  if (error || !entry.file) {
    return <span className={styles.previewMissing}>{error ? "Preview unavailable — the file could not be read." : "Selected file is unavailable"}</span>;
  }
  const onError = () => setError(true);
  if (audio) return <audio className={styles.chosenAudio} controls preload="metadata" src={previewUrl} onError={onError} />;
  if (entry.kind === "image") return <img className={styles.chosenThumb} src={previewUrl} alt={entry.description || entry.asset_id} loading="lazy" onError={onError} />;
  if (entry.kind === "video") return <video className={styles.chosenThumb} src={previewUrl} muted preload="metadata" onError={onError} />;
  return null;
}

function LibraryPicker({
  role,
  videoId,
  initial,
  onSaved,
}: {
  role: OwnerMediaRole;
  videoId: string;
  initial: string[];
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const suggestion = useMutation({ mutationFn: () => suggestMedia(videoId, role), onSuccess: result => { setChosen(result.assets.map(asset => asset.id)); setError(result.assets.length ? null : "No suitable cleared media found. Browse the library or discover external media."); } });
  const [chosen, setChosen] = useState<string[]>(initial);
  const [annotating, setAnnotating] = useState<string | null>(null);
  const [showBlocked, setShowBlocked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const listing = useQuery({
    queryKey: ["library", query],
    queryFn: () => listLibrary({ query }),
  });

  const save = useMutation({
    mutationFn: () => setOwnerMedia(videoId, { [role]: chosen }),
    onSuccess: onSaved,
    onError: (e: unknown) => setError(e instanceof Error ? e.message : "Could not save the selection."),
  });

  const forRole = useMemo(
    () => (listing.data?.assets ?? []).filter((a) => a.kind && ROLE_KIND[role].includes(a.kind)),
    [listing.data, role],
  );
  // A real library is mostly things that are not ready: not described yet,
  // no rights recorded, or sitting on a machine this host cannot read.
  // Showing all of it at once buries the handful that can actually be used,
  // so the rest is one click away with its count stated plainly.
  const ready = forRole.filter((a) => a.selectable || chosen.includes(a.id));
  const blocked = forRole.filter((a) => !a.selectable && !chosen.includes(a.id));
  const assets = showBlocked ? [...ready, ...blocked] : ready;

  const toggle = (asset: LibraryAsset) => {
    setError(null);
    setChosen((current) =>
      current.includes(asset.id) ? current.filter((id) => id !== asset.id) : [...current, asset.id],
    );
  };

  const changed =
    chosen.length !== initial.length || chosen.some((id, index) => id !== initial[index]);

  return (
    <div className={styles.picker}>
      <div className={styles.pickerBar}>
        <Button size="sm" onClick={() => suggestion.mutate()} disabled={suggestion.isPending}>{suggestion.isPending ? "Matching your direction…" : "Suggest for this production"}</Button>
        <label className={styles.search}>
          <span className={styles.srOnly}>Search your {ROLE_LABEL[role].toLowerCase()} library</span>
          <input
            type="search"
            value={query}
            placeholder={`Search ${ROLE_KIND[role].join(" / ")} by description or tag`}
            onChange={(e) => setQuery(e.target.value)}
            className={styles.searchInput}
          />
        </label>
        <ScanFolder onScanned={() => queryClient.invalidateQueries({ queryKey: ["library"] })} />
      </div>

      {listing.isLoading && <InlineSpinner label="Reading the library…" />}
      {suggestion.data && <p className={styles.roleHint}>{suggestion.data.reason} Review the selection below, then choose Use this media.</p>}
      {suggestion.isError && <p role="alert">{suggestion.error.message}</p>}
      {listing.isError && (
        <ErrorState
          compact
          title="Could not read the library"
          where="GET /api/v1/library/"
          detail={(listing.error as Error).message}
        />
      )}

      {listing.data && assets.length === 0 && (
        <EmptyState
          compact
          title={
            query
              ? "Nothing matches that"
              : blocked.length > 0
                ? `Nothing in your library is ready for ${ROLE_LABEL[role].toLowerCase()} yet`
                : `No ${ROLE_KIND[role].join(" or ")} in your library yet`
          }
          description={
            query
              ? "Descriptions and tags are searched — not filenames, which say nothing reliable about content."
              : "Catalog a folder of your own files above. Scanning reads them: nothing is moved, renamed or changed."
          }
        />
      )}

      {assets.length > 0 && (
        <ul className={styles.grid}>
          {assets.map((asset) => (
            <AssetCard
              key={asset.id}
              asset={asset}
              selected={chosen.includes(asset.id)}
              position={role === "visuals" ? chosen.indexOf(asset.id) + 1 : 0}
              onToggle={() => toggle(asset)}
              annotating={annotating === asset.id}
              onAnnotate={() => setAnnotating(annotating === asset.id ? null : asset.id)}
              onAnnotated={() => {
                setAnnotating(null);
                queryClient.invalidateQueries({ queryKey: ["library"] });
              }}
            />
          ))}
        </ul>
      )}

      {blocked.length > 0 && (
        <button type="button" className={styles.moreToggle} onClick={() => setShowBlocked((v) => !v)}
                aria-expanded={showBlocked}>
          {showBlocked
            ? `Hide the ${blocked.length} that cannot be used yet`
            : `${blocked.length} more need details or are on another machine — show`}
        </button>
      )}

      <div className={styles.pickerFoot}>
        <p className={styles.pickerCount}>
          {chosen.length === 0
            ? `Nothing chosen — ${ROLE_LABEL[role].toLowerCase()} stays generated.`
            : `${chosen.length} chosen${role === "visuals" ? ", used in this order and repeated if there are more shots" : ""}.`}
        </p>
        <div className={styles.pickerActions}>
          {chosen.length > 0 && (
            <Button size="sm" variant="ghost" onClick={() => setChosen([])}>
              Clear
            </Button>
          )}
          <Button
            size="sm"
            variant="primary"
            disabled={!changed || save.isPending}
            onClick={() => {
              setError(null);
              save.mutate();
            }}
          >
            {save.isPending ? "Saving…" : "Use this media"}
          </Button>
        </div>
      </div>
      {error && (
        <p role="alert" className={styles.pickerError}>
          {error}
        </p>
      )}
    </div>
  );
}

export function AssetCard({
  asset,
  selected,
  position,
  onToggle,
  annotating,
  onAnnotate,
  onAnnotated,
}: {
  asset: LibraryAsset;
  selected: boolean;
  position: number;
  onToggle: () => void;
  annotating: boolean;
  onAnnotate: () => void;
  onAnnotated: () => void;
}) {
  const [previewError, setPreviewError] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const url = libraryFileUrl(asset.id);
  const stateLabel = asset.state === "ready" ? "Ready to use" :
    asset.state === "unavailable" ? "Unavailable here" :
      asset.state === "processing" ? "Processing" : "Needs attention";
  const stateTone = asset.state === "ready" ? "success" : asset.state === "unavailable" ? "danger" : "warning";
  return (
    <li className={`${styles.card} ${selected ? styles.cardSelected : ""} ${asset.selectable ? "" : styles.cardBlocked}`}>
      <div className={styles.preview}>
        {(asset.kind === "image" || asset.preview_kind === "image") && (asset.available || asset.preview_available) && !previewError && (
          <button type="button" className={styles.expandImage} onClick={() => setExpanded(true)} aria-label={`Inspect ${asset.description || asset.filename}`}><img className={styles.previewMedia} src={url} alt={asset.description || "library image"} loading="lazy"
               onError={() => setPreviewError(true)} /></button>
        )}
        {asset.kind === "video" && asset.available && asset.preview_kind !== "image" && !previewError && (
          <video className={styles.previewMedia} src={url} controls muted preload="metadata"
                 onError={() => setPreviewError(true)} />
        )}
        {asset.kind === "audio" && (asset.available || asset.preview_available) && !previewError && (
          <audio className={styles.previewAudio} controls preload="none" src={url}
                 onError={() => setPreviewError(true)} />
        )}
        {!asset.available && !asset.preview_available && <span className={styles.previewMissing}>Preview arrives when your computer connects</span>}
        {previewError && <span className={styles.previewMissing}>Preview unavailable — the file could not be read.</span>}
        {position > 0 && <span className={styles.badgeOrder}>{position}</span>}
      </div>

      <div className={styles.cardBody}>
        {expanded && <MediaDialog title={asset.description || asset.filename} onClose={() => setExpanded(false)}><img src={url} alt={asset.description || asset.filename} /></MediaDialog>}
        <p className={styles.cardDescription}>
          {asset.description || <span className={styles.undescribed}>Not described yet</span>}
        </p>
        <p className={styles.cardMeta} title={asset.resolved_path ?? undefined}>
          {asset.filename || asset.id.slice(0, 12)} · {asset.kind}
          {assetSummary(asset) ? ` · ${assetSummary(asset)}` : ""}
        </p>
        <Badge tone={stateTone}>{stateLabel}</Badge>
        {asset.rights && (
          <>
            <Badge tone={asset.rights.commercial_use ? "success" : "warning"}>
              {asset.rights.commercial_use ? "cleared for commercial use" : "not cleared"}
            </Badge>
            {/* The licence as recorded, verbatim. "unknown" is a real and
                useful answer here, and dressing it up as a clearance is
                exactly what this project does not do. */}
            <p className={styles.cardMeta}>
              Licence: {asset.rights.license || "not recorded"}
              {asset.rights.attribution_required ? " · attribution required" : ""}
            </p>
          </>
        )}
        {asset.problems.length > 0 && (
          <ul className={styles.problems}>
            {asset.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        )}
      </div>

      <div className={styles.cardActions}>
        <Button
          size="sm"
          variant={selected ? "success" : "secondary"}
          onClick={onToggle}
          disabled={!asset.selectable}
          title={asset.selectable ? undefined : asset.problems.join("; ")}
          aria-pressed={selected}
        >
          {selected ? "Chosen" : "Choose"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onAnnotate} aria-expanded={annotating}>
          {asset.selectable ? "Edit details" : "Review details"}
        </Button>
      </div>

      {annotating && <AnnotationForm asset={asset} onAnnotated={onAnnotated} />}
    </li>
  );
}

// The owner's own account of an asset: what it shows, where it came from and
// what rights they hold. Newly scanned owner files receive a declared local
// default; genuinely unknown third-party terms still stay unselectable.
function AnnotationForm({ asset, onAnnotated }: { asset: LibraryAsset; onAnnotated: () => void }) {
  const [description, setDescription] = useState(asset.description);
  const [source, setSource] = useState(asset.source);
  const [tags, setTags] = useState(asset.tags.join(", "));
  const [license, setLicense] = useState(asset.rights?.license ?? "");
  const [rightsSource, setRightsSource] = useState(asset.rights?.source ?? "");
  const [evidence, setEvidence] = useState(asset.rights?.evidence ?? "");
  const [commercial, setCommercial] = useState(asset.rights?.commercial_use === true);
  const [attribution, setAttribution] = useState(asset.rights?.attribution_text ?? "");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      annotateAsset(asset.id, {
        description,
        source,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        origin: "owner",
        rights:
          license.trim() && rightsSource.trim() && evidence.trim()
            ? {
                source: rightsSource,
                license,
                commercial_use: commercial,
                evidence,
                attribution_required: !!attribution.trim(),
                attribution_text: attribution.trim() || null,
              }
            : null,
      }),
    onSuccess: onAnnotated,
    onError: (e: unknown) => setError(e instanceof Error ? e.message : "Could not save."),
  });

  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        mutation.mutate();
      }}
    >
      <label className={styles.field}>
        <span>What it shows</span>
        <input value={description} onChange={(e) => setDescription(e.target.value)} required
               placeholder="Rain on a window at night, handheld" />
      </label>
      <label className={styles.field}>
        <span>Where it came from</span>
        <input value={source} onChange={(e) => setSource(e.target.value)} required
               placeholder="Shot by me, 2026-04 · or a download page" />
      </label>
      <label className={styles.field}>
        <span>Tags</span>
        <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="rain, night, calm" />
      </label>
      <fieldset className={styles.rights}>
        <legend>Rights — required before it can be used</legend>
        <label className={styles.field}>
          <span>Licence or permission</span>
          <input value={license} onChange={(e) => setLicense(e.target.value)}
                 placeholder="My own work · CC0 · purchased licence" />
        </label>
        <label className={styles.field}>
          <span>Rights holder / source record</span>
          <input value={rightsSource} onChange={(e) => setRightsSource(e.target.value)}
                 placeholder="Me · creator name · provider" />
        </label>
        <label className={styles.field}>
          <span>Evidence</span>
          <input value={evidence} onChange={(e) => setEvidence(e.target.value)}
                 placeholder="Receipt, licence text, or that you created it" />
        </label>
        <label className={styles.check}>
          <input type="checkbox" checked={commercial} onChange={(e) => setCommercial(e.target.checked)} />
          <span>Commercial use is allowed</span>
        </label>
        <label className={styles.field}>
          <span>Attribution text (if required)</span>
          <input value={attribution} onChange={(e) => setAttribution(e.target.value)} />
        </label>
      </fieldset>
      <div className={styles.formActions}>
        <Button size="sm" variant="primary" type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? "Saving…" : "Save details"}
        </Button>
      </div>
      {error && (
        <p role="alert" className={styles.pickerError}>
          {error}
        </p>
      )}
    </form>
  );
}

export function ScanFolder({ onScanned }: { onScanned: () => void }) {
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => scanLibrary(path),
    onSuccess: (report) => {
      setResult(
        `${report.indexed} file${report.indexed === 1 ? "" : "s"} cataloged` +
          (report.issues.length ? `, ${report.issues.length} refused` : ""),
      );
      onScanned();
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : "Scan failed."),
  });

  if (!open) {
    return (
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
        Catalog a folder
      </Button>
    );
  }
  return (
    <form
      className={styles.scan}
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        setResult(null);
        mutation.mutate();
      }}
    >
      <label className={styles.field}>
        <span>Folder or file on this machine</span>
        <input value={path} onChange={(e) => setPath(e.target.value)} required
               placeholder="/home/you/Videos/dreamdrip/assets" />
      </label>
      <Button size="sm" variant="primary" type="submit" disabled={mutation.isPending || !path.trim()}>
        {mutation.isPending ? "Inspecting…" : "Scan"}
      </Button>
      <p className={styles.scanNote}>
        Every file is decoded to prove it is usable, so a large folder takes a while. Your originals are only read.
      </p>
      {result && <p className={styles.scanResult}>{result}</p>}
      {error && (
        <p role="alert" className={styles.pickerError}>
          {error}
        </p>
      )}
    </form>
  );
}

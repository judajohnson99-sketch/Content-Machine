import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fileUrl, getAssets } from "../api/assets";
import { Badge } from "./ui/Badge";
import { InlineSpinner } from "./ui/States";
import { formatDateTime } from "../lib/format";
import type { ImageAsset, ProjectAssets } from "../types/assets";
import styles from "./AssetsPanel.module.css";

interface Props {
  videoId: string;
}

// Every generated artefact a project holds, with its lineage: scene images
// (which scene each serves, which provider/worker/prompt made it), the
// composed audio and its layers' licences, the storyboard's scene plan, and
// the run logs. Read-only over project_assets(); provenance claims are
// shown, never made, here.
export function AssetsPanel({ videoId }: Props) {
  const query = useQuery({
    queryKey: ["project-assets", videoId],
    queryFn: () => getAssets(videoId),
    refetchInterval: 15_000,
  });

  if (query.isLoading) return <InlineSpinner label="Loading assets…" />;
  if (query.isError) {
    return <p role="alert">Failed to load assets: {(query.error as Error).message}</p>;
  }
  const assets = query.data!;

  return (
    <div className={styles.sections}>
      <ImagesSection videoId={videoId} assets={assets} />
      <AudioSection videoId={videoId} assets={assets} />
      <StoryboardSection assets={assets} />
      <LogsSection videoId={videoId} assets={assets} />
    </div>
  );
}

function ImagesSection({ videoId, assets }: { videoId: string; assets: ProjectAssets }) {
  const [open, setOpen] = useState<string | null>(null);
  const prov = assets.images_provenance;
  const grade = prov?.production_grade;
  return (
    <section>
      <div className={styles.sectionHead}>
        <h4 className={styles.sectionTitle}>
          Images <span className={styles.count}>{assets.images.length}</span>
        </h4>
        {prov?.provider && assets.images.length > 0 && (
          <span className={styles.provRow}>
            <span className={styles.muted}>via {prov.provider}{prov.model ? ` · ${prov.model}` : ""}</span>
            <Badge tone={grade === true ? "success" : grade === false ? "danger" : "warning"}>
              {grade === true ? "production-grade (human claim)" : grade === false ? "not production-grade" : "grade undecided"}
            </Badge>
          </span>
        )}
      </div>
      {assets.visual_plan?.prompt && (
        <p className={styles.prompt} title={assets.visual_plan.negative_prompt ?? undefined}>
          <span className={styles.promptLabel}>Prompt</span> {assets.visual_plan.prompt}
        </p>
      )}
      {assets.images.length === 0 && <p className={styles.muted}>No images yet — run Visuals or Scenes.</p>}
      {assets.images.length > 0 && (
        <div className={styles.grid}>
          {assets.images.map((img) => (
            <ImageTile
              key={img.path}
              videoId={videoId}
              image={img}
              open={open === img.path}
              onToggle={() => setOpen(open === img.path ? null : img.path)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function ImageTile({ videoId, image, open, onToggle }: { videoId: string; image: ImageAsset; open: boolean; onToggle: () => void }) {
  const gen = image.generation;
  const name = image.path.replace(/^images\//, "");
  return (
    <figure className={`${styles.tile} ${open ? styles.tileOpen : ""}`}>
      <button type="button" className={styles.tileButton} onClick={onToggle} aria-expanded={open} aria-label={`Details for ${name}`}>
        <img className={styles.tileImage} src={fileUrl(videoId, image.path)} alt={image.path} loading="lazy" />
        <span className={styles.tileOverlay}>
          {image.scene_id && <span className={styles.sceneTag}>{image.scene_id}</span>}
          {gen?.provider && (
            <span className={`${styles.providerTag} ${gen.produces_depicted ? styles.providerDepicted : styles.providerAbstract}`}>
              {gen.provider}{gen.worker_id ? ` · ${gen.worker_id}` : ""}
            </span>
          )}
        </span>
      </button>
      <figcaption className={styles.tileCaption} title={image.path}>
        {name}
      </figcaption>
      {open && (
        <dl className={styles.lineage}>
          {gen ? (
            <>
              {gen.prompt && <div><dt>Prompt</dt><dd>{gen.prompt}</dd></div>}
              {gen.negative_prompt && <div><dt>Negative</dt><dd>{gen.negative_prompt}</dd></div>}
              <div><dt>Provider</dt><dd>{gen.provider ?? "—"}{gen.model ? ` · ${gen.model}` : ""}</dd></div>
              {gen.worker_id && <div><dt>Worker</dt><dd>{gen.worker_id}</dd></div>}
              <div><dt>Request</dt><dd>{gen.width}×{gen.height} · seed {gen.seed}</dd></div>
              <div><dt>Job</dt><dd className={styles.mono}>{gen.job_id}</dd></div>
              {gen.completed_at && <div><dt>Generated</dt><dd>{formatDateTime(gen.completed_at)}</dd></div>}
              {gen.notes && <div><dt>Notes</dt><dd>{gen.notes}</dd></div>}
            </>
          ) : (
            <div><dt>Lineage</dt><dd>No generation record for this file (ingested or made outside the router).</dd></div>
          )}
        </dl>
      )}
    </figure>
  );
}

function AudioSection({ videoId, assets }: { videoId: string; assets: ProjectAssets }) {
  const audio = assets.audio;
  return (
    <section>
      <h4 className={styles.sectionTitle}>Audio</h4>
      {!audio && <p className={styles.muted}>No audio track yet — run Audio.</p>}
      {audio && (
        <div className={styles.audioBlock}>
          <audio className={styles.audio} controls preload="none" src={fileUrl(videoId, audio.path)} />
          <div className={styles.audioMeta}>
            {audio.seconds != null && <span>{audio.seconds.toFixed(1)}s</span>}
            {audio.mean_volume_db != null && <span>mean {audio.mean_volume_db.toFixed(1)} dB</span>}
            {audio.commercial_use_cleared != null && (
              <Badge tone={audio.commercial_use_cleared ? "success" : "danger"}>
                {audio.commercial_use_cleared ? "commercial use cleared" : "NOT cleared for commercial use"}
              </Badge>
            )}
            {audio.layers.map((layer) => (
              <span key={`${layer.id}-${layer.provider}`} className={styles.layer}>
                {layer.id ?? "layer"}: {layer.provider ?? "?"}
                {layer.voice ? ` (${layer.voice})` : ""}
                {layer.license ? ` · ${layer.license}` : ""}
              </span>
            ))}
          </div>
          {audio.attributions_required.length > 0 && (
            <p className={styles.muted}>Attribution required: {audio.attributions_required.join("; ")}</p>
          )}
        </div>
      )}
    </section>
  );
}

function StoryboardSection({ assets }: { assets: ProjectAssets }) {
  const board = assets.storyboard;
  return (
    <section>
      <h4 className={styles.sectionTitle}>
        Storyboard{" "}
        {board && (
          <span className={styles.count}>
            {board.scene_count} scene{board.scene_count === 1 ? "" : "s"}
            {board.timeline_seconds != null ? ` · ${board.timeline_seconds.toFixed(1)}s timeline` : ""}
          </span>
        )}
      </h4>
      {!board && <p className={styles.muted}>No storyboard — this project renders its image set directly.</p>}
      {board && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Scene</th>
                <th>Section</th>
                <th>Duration</th>
                <th>Motion</th>
                <th>Transition</th>
                <th>Image</th>
              </tr>
            </thead>
            <tbody>
              {board.scenes.map((s) => (
                <tr key={s.scene_id}>
                  <td className={styles.mono}>{s.scene_id}</td>
                  <td>{s.section ?? "—"}</td>
                  <td>{s.duration_seconds != null ? `${s.duration_seconds.toFixed(1)}s` : "—"}</td>
                  <td>{s.motion ?? "—"}</td>
                  <td>{s.transition ?? "—"}</td>
                  <td>
                    {s.image ? (
                      s.image.replace(/^images\//, "")
                    ) : s.job_id ? (
                      <Badge tone="warning">queued · {s.job_id.slice(0, 8)}</Badge>
                    ) : (
                      <span className={styles.muted}>missing</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function LogsSection({ videoId, assets }: { videoId: string; assets: ProjectAssets }) {
  if (assets.logs.length === 0) return null;
  return (
    <section>
      <h4 className={styles.sectionTitle}>
        Run logs <span className={styles.count}>latest {assets.logs.length}</span>
      </h4>
      <div className={styles.logs}>
        {assets.logs.map((log) => (
          <a key={log.path} className={styles.logLink} href={fileUrl(videoId, log.path)} target="_blank" rel="noreferrer">
            {log.path.replace(/^logs\//, "")} · {formatDateTime(log.modified_utc)}
          </a>
        ))}
      </div>
    </section>
  );
}

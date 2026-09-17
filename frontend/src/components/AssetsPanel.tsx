import { useQuery } from "@tanstack/react-query";
import { fileUrl, getAssets } from "../api/assets";
import { InlineSpinner } from "./ui/States";
import { formatDateTime } from "../lib/format";
import type { ProjectAssets } from "../types/assets";
import styles from "./AssetsPanel.module.css";

interface Props {
  videoId: string;
}

// Every generated artefact a project holds, with its lineage: scene images
// (and which scene each serves), the composed audio and its layers'
// licences, the storyboard's scene plan, and the run logs. Read-only over
// project_assets(); provenance claims are shown, never made, here.
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
  return (
    <section>
      <h4 className={styles.sectionTitle}>
        Images <span className={styles.count}>{assets.images.length}</span>
      </h4>
      {assets.images.length === 0 && <p className={styles.muted}>No images yet — run Visuals or Scenes.</p>}
      {assets.images.length > 0 && (
        <div className={styles.grid}>
          {assets.images.map((img) => (
            <figure key={img.path} className={styles.tile}>
              <img className={styles.tileImage} src={fileUrl(videoId, img.path)} alt={img.path} loading="lazy" />
              <figcaption className={styles.tileCaption} title={img.path}>
                {img.scene_id && <span className={styles.sceneTag}>{img.scene_id} · </span>}
                {img.path.replace(/^images\//, "")}
              </figcaption>
            </figure>
          ))}
        </div>
      )}
    </section>
  );
}

function AudioSection({ videoId, assets }: { videoId: string; assets: ProjectAssets }) {
  const audio = assets.audio;
  return (
    <section>
      <h4 className={styles.sectionTitle}>Audio</h4>
      {!audio && <p className={styles.muted}>No audio track yet — run Audio.</p>}
      {audio && (
        <div>
          <audio className={styles.audio} controls preload="none" src={fileUrl(videoId, audio.path)} />
          <div className={styles.audioMeta}>
            {audio.seconds != null && <span>{audio.seconds.toFixed(1)}s</span>}
            {audio.mean_volume_db != null && <span>mean {audio.mean_volume_db.toFixed(1)} dB</span>}
            {audio.commercial_use_cleared != null && (
              <span>{audio.commercial_use_cleared ? "commercial use cleared" : "NOT cleared for commercial use"}</span>
            )}
            {audio.layers.map((layer) => (
              <span key={`${layer.id}-${layer.provider}`} className={styles.layer}>
                {layer.id ?? "layer"}: {layer.provider ?? "?"}
                {layer.voice ? ` (${layer.voice})` : ""}
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
                <td>{s.scene_id}</td>
                <td>{s.section ?? "—"}</td>
                <td>{s.duration_seconds != null ? `${s.duration_seconds.toFixed(1)}s` : "—"}</td>
                <td>{s.motion ?? "—"}</td>
                <td>{s.transition ?? "—"}</td>
                <td>{s.image ? s.image.replace(/^images\//, "") : "missing"}</td>
              </tr>
            ))}
          </tbody>
        </table>
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

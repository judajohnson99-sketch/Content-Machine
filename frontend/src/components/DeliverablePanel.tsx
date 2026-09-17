import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fileUrl, getAssets } from "../api/assets";
import { StatusBadge } from "./StatusBadge";
import { InlineSpinner } from "./ui/States";
import { formatDateTime } from "../lib/format";
import type { ProjectAssets } from "../types/assets";
import styles from "./DeliverablePanel.module.css";

interface Props {
  videoId: string;
  // Compact: one column, no per-check QC list unless expanded - for the
  // Review Center, where the decision controls sit right below it.
  compact?: boolean;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(2)} GB`;
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  if (bytes >= 1 << 10) return `${(bytes / (1 << 10)).toFixed(0)} KB`;
  return `${bytes} B`;
}

// The finished deliverable and the evidence behind its verdict: the render
// itself, its thumbnails, the QC report and the publication package's own
// blocking list - all read from project_assets(), never re-derived here.
export function DeliverablePanel({ videoId, compact = false }: Props) {
  const query = useQuery({
    queryKey: ["project-assets", videoId],
    queryFn: () => getAssets(videoId),
    refetchInterval: 15_000,
  });

  if (query.isLoading) return <InlineSpinner label="Loading deliverable…" />;
  if (query.isError) {
    return <p role="alert">Failed to load assets: {(query.error as Error).message}</p>;
  }
  const assets = query.data!;

  return (
    <div className={`${styles.panel} ${compact ? styles.compact : ""}`}>
      <div>
        <VideoBlock videoId={videoId} assets={assets} />
      </div>
      <div className={styles.side}>
        <QcBlock assets={assets} compact={compact} />
        <PackageBlock assets={assets} />
      </div>
    </div>
  );
}

function VideoBlock({ videoId, assets }: { videoId: string; assets: ProjectAssets }) {
  const poster = assets.thumbnails[0] ? fileUrl(videoId, assets.thumbnails[0].path) : undefined;
  return (
    <div>
      {assets.video ? (
        <video
          className={styles.player}
          controls
          preload="metadata"
          poster={poster}
          src={fileUrl(videoId, assets.video.path)}
          data-testid="deliverable-video"
        />
      ) : (
        <div className={styles.placeholder} data-testid="deliverable-missing">
          No render yet — run Assemble &amp; Render (or Produce) to create the deliverable.
        </div>
      )}
      {assets.video && (
        <div className={styles.meta}>
          <span>{assets.video.path}</span>
          <span>{formatBytes(assets.video.bytes)}</span>
          <span>rendered {formatDateTime(assets.video.modified_utc)}</span>
        </div>
      )}
      {assets.thumbnails.length > 0 && (
        <div className={styles.thumbs} aria-label="Thumbnail candidates">
          {assets.thumbnails.map((t) => (
            <img key={t.path} className={styles.thumb} src={fileUrl(videoId, t.path)} alt={t.path} loading="lazy" />
          ))}
        </div>
      )}
    </div>
  );
}

function QcBlock({ assets, compact }: { assets: ProjectAssets; compact: boolean }) {
  const [showAll, setShowAll] = useState(!compact);
  const qc = assets.qc;
  if (!qc) {
    return (
      <div>
        <h4 className={styles.sectionTitle}>Quality control</h4>
        <p className={styles.qcRow}>Not run yet.</p>
      </div>
    );
  }
  const failed = qc.checks.filter((c) => !c.passed);
  const visible = showAll ? qc.checks : failed;
  return (
    <div>
      <h4 className={styles.sectionTitle}>Quality control</h4>
      <div className={styles.qcRow}>
        <StatusBadge status={qc.status ?? "UNKNOWN"} />
        <span>
          {qc.checks_run ?? qc.checks.length} checks, {qc.checks_failed ?? failed.length} failed
        </span>
      </div>
      {visible.length > 0 && (
        <ul className={styles.checkList}>
          {visible.map((c) => (
            <li key={c.check} className={`${styles.check} ${c.passed ? "" : styles.checkFailed}`}>
              <span className={styles.checkName}>{c.passed ? "✓" : "✗"} {c.check}</span>
              {c.detail && <span className={styles.checkDetail} title={c.detail}>{c.detail}</span>}
            </li>
          ))}
        </ul>
      )}
      {qc.checks.length > failed.length && (
        <button type="button" className={styles.toggle} onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show failures only" : `Show all ${qc.checks.length} checks`}
        </button>
      )}
    </div>
  );
}

function PackageBlock({ assets }: { assets: ProjectAssets }) {
  const pkg = assets.package;
  if (!pkg) return null;
  return (
    <div>
      <h4 className={styles.sectionTitle}>Publication package</h4>
      <div className={styles.qcRow}>
        <StatusBadge status={pkg.status ?? "UNKNOWN"} />
        <span>generated {formatDateTime(pkg.generated_utc)}</span>
      </div>
      {pkg.blocking_issues.length > 0 && (
        <ul className={styles.blockers}>
          {pkg.blocking_issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

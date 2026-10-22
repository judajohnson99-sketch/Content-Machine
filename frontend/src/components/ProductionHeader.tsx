import { Link } from "react-router-dom";
import type { ProjectAssets } from "../types/assets";
import type { ProjectDetail, StatusReport } from "../types/project";
import type { LatestRuns } from "../types/pipeline";
import { deriveProgress, nextAction, type Phase } from "../lib/projectPhase";
import { fileUrl } from "../api/assets";
import { StatusBadge } from "./StatusBadge";
import { StageActionButton } from "./StageActionButton";
import { Button } from "./ui/Button";
import { FilmIcon } from "./ui/icons";
import styles from "./ProductionHeader.module.css";

/**
 * The orientation panel at the top of a production.
 *
 * It exists to answer four questions at a glance - what am I making, where
 * am I, what's blocking me, what should I do next - before any pipeline
 * vocabulary appears. Everything technical (stage names, the raw verdict,
 * the project id) stays available in the disclosure at the bottom.
 */
export function ProductionHeader({
  videoId,
  project,
  assets,
  status,
  runs,
  compact = false,
}: {
  videoId: string;
  project: ProjectDetail | undefined;
  assets: ProjectAssets | undefined;
  status: StatusReport | undefined;
  runs: LatestRuns | undefined;
  compact?: boolean;
}) {
  const progress = deriveProgress(assets, status, runs);
  const next = nextAction(progress, status, videoId);
  const still = assets?.thumbnails[0] ?? assets?.images[0] ?? null;
  const niche = project?.experiment?.niche?.replace(/_/g, " ");

  const durationSeconds = assets?.storyboard?.timeline_seconds ?? assets?.audio?.seconds ?? null;

  return (
    <section className={styles.header} aria-label="Production overview">
      <div className={styles.top}>
        <div className={styles.still}>
          {still ? (
            <img className={styles.stillImage} src={fileUrl(videoId, still.path)} alt="" loading="lazy" />
          ) : (
            <div className={styles.stillEmpty}>
              <FilmIcon width={22} height={22} />
            </div>
          )}
        </div>

        <div className={styles.identity}>
          <p className={styles.eyebrow}>{niche ? niche : "Production"}</p>
          <h1 className={styles.title}>{project?.selected_title || videoId}</h1>
          <p className={styles.facts}>
            <span>{progress.current.label}</span>
            {durationSeconds ? <span>{formatMinutes(durationSeconds)}</span> : null}
            {assets?.storyboard ? <span>{assets.storyboard.scene_count} scenes</span> : null}
            {status ? <StatusBadge status={status.verdict} /> : null}
          </p>
        </div>

        <div className={styles.next}>
          {progress.busy ? (
            <span className={styles.busy}>Working…</span>
          ) : next ? (
            <>
              {next.stage ? (
                <StageActionButton videoId={videoId} stage={next.stage} label={next.label} />
              ) : (
                <Link to={next.to!}>
                  <Button variant="primary" size="lg">
                    {next.label}
                  </Button>
                </Link>
              )}
              <span className={styles.why}>{next.why}</span>
            </>
          ) : null}
        </div>
      </div>

      {!compact && <ol className={styles.phases}>
        {progress.phases.map((phase) => (
          <PhaseCell key={phase.id} phase={phase} />
        ))}
      </ol>}

      <details className={styles.technical}>
        <summary>Technical details</summary>
        <div className={styles.technicalBody}>
          <span>{videoId}</span>
          {project?.experiment?.concept_id && <span>concept: {project.experiment.concept_id}</span>}
          {status && <span>verdict: {status.verdict}</span>}
          {status?.stale && <span>recorded: {status.recorded} (stale)</span>}
          {status?.digest_state && <span>digest: {status.digest_state}</span>}
        </div>
      </details>
    </section>
  );
}

const MARK: Record<Phase["state"], { label: string; className: string }> = {
  done: { label: "done", className: styles.markDone },
  active: { label: "working", className: styles.markActive },
  blocked: { label: "blocked", className: styles.markBlocked },
  todo: { label: "", className: "" },
};

function PhaseCell({ phase }: { phase: Phase }) {
  const tone =
    phase.state === "done"
      ? styles.phaseDone
      : phase.state === "active"
        ? styles.phaseActive
        : phase.state === "blocked"
          ? styles.phaseBlocked
          : "";
  const mark = MARK[phase.state];
  return (
    <li className={`${styles.phase} ${tone}`}>
      <span className={styles.phaseName}>
        {phase.label}
        {mark.label && <span className={`${styles.phaseMark} ${mark.className}`}>{mark.label}</span>}
      </span>
      <p className={styles.phaseSummary}>{phase.summary}</p>
    </li>
  );
}

function formatMinutes(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes.toFixed(minutes < 10 ? 1 : 0)} min`;
  return `${(minutes / 60).toFixed(1)} h`;
}

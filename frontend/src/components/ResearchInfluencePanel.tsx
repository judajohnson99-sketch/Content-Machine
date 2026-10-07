import { useQuery } from "@tanstack/react-query";
import { getResearchInfluence } from "../api/research";
import { Badge } from "./ui/Badge";
import { EmptyState, SkeletonRows } from "./ui/States";
import { SearchIcon } from "./ui/icons";
import type { ResearchDecision } from "../types/research";
import styles from "./ResearchInfluencePanel.module.css";

// What each directive is, in the operator's language rather than the
// parameter name the pipeline uses. A parameter with no entry here is still
// shown - the list is a courtesy, not a filter, so a new directive never
// goes invisible because this map was not updated.
const PARAMETER_LABEL: Record<string, string> = {
  seconds_per_scene: "How long each shot is held",
  transition_seconds: "How long each dissolve runs",
  motion_style: "How the camera moves",
  typical_duration_seconds: "How long videos in this niche run",
  audio_emphasis: "Which sound layers lead",
  scene_count: "How many shots this video is cut into",
};

function describeValue(parameter: string, value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value !== "number") return String(value);
  if (parameter === "typical_duration_seconds") {
    return value >= 3600
      ? `${(value / 3600).toFixed(1)} hours`
      : `${Math.round(value / 60)} minutes`;
  }
  if (parameter.endsWith("_seconds")) return `${value}s`;
  return String(value);
}

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function Decision({ decision, applied }: { decision: ResearchDecision; applied: boolean }) {
  return (
    <li className={styles.decision}>
      <div className={styles.decisionHead}>
        <span className={styles.parameter}>
          {PARAMETER_LABEL[decision.parameter] ?? decision.parameter}
        </span>
        <span className={styles.value}>{describeValue(decision.parameter, decision.value)}</span>
        <Badge tone={applied ? "success" : "neutral"}>
          {applied ? "applied to this build" : "recorded, not applied"}
        </Badge>
      </div>
      <p className={styles.rationale}>{decision.rationale}</p>
      <div className={styles.evidence}>
        <Badge tone={decision.confidence === "VERIFIED" ? "success" : "warning"}>
          {decision.confidence.toLowerCase()}
        </Badge>
        {decision.source_urls.slice(0, 4).map((url) => (
          <a key={url} href={url} target="_blank" rel="noreferrer" className={styles.source}>
            {hostname(url)}
          </a>
        ))}
        {decision.source_urls.length > 4 && (
          <span className={styles.more}>+{decision.source_urls.length - 4} more</span>
        )}
      </div>
    </li>
  );
}

// Shows what sourced research changed about this production, and - just as
// importantly - what it did not. "Research-driven" is a claim, and this is
// where a person checks it: every directive carries the sentences and URLs
// it came from, and whether the build actually used it.
export function ResearchInfluencePanel({ videoId }: { videoId: string }) {
  const query = useQuery({
    queryKey: ["research-influence", videoId],
    queryFn: () => getResearchInfluence(videoId),
    enabled: !!videoId,
  });

  if (query.isLoading) return <SkeletonRows count={2} />;

  const influence = query.data;
  if (!influence || influence.researched === false) {
    return (
      <EmptyState
        icon={<SearchIcon width={22} height={22} />}
        title="Research has not run yet"
        description="Run research to find out how this niche is actually made, and to let those findings set pacing, movement, dissolves and the sound layers."
      />
    );
  }

  if (influence.decisions.length === 0) {
    return (
      <p className={styles.nothing}>
        Research ran, and nothing it found changes a production parameter. The
        stage defaults stand - which is the honest outcome, not a failure.
      </p>
    );
  }

  const appliedKeys = new Set(Object.keys(influence.applied));
  return (
    <div>
      <ul className={styles.list}>
        {influence.decisions.map((decision) => (
          <Decision
            key={`${decision.parameter}-${decision.value}`}
            decision={decision}
            applied={appliedKeys.has(decision.parameter)}
          />
        ))}
      </ul>
      {influence.scene_count != null && (
        <p className={styles.outcome}>
          This plan is {influence.scene_count} shot{influence.scene_count === 1 ? "" : "s"}
          {influence.timeline_seconds != null &&
            ` across ${Math.round(influence.timeline_seconds / 60)} minutes`}
          .
        </p>
      )}
      {influence.suggested_not_applied.length > 0 && (
        <p className={styles.notApplied}>
          Recorded but not applied by the current build:{" "}
          {influence.suggested_not_applied
            .map((p) => PARAMETER_LABEL[p] ?? p)
            .join(", ")}
          . Re-run the storyboard to pick these up.
        </p>
      )}
    </div>
  );
}

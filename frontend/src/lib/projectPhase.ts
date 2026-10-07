// The five phases a production moves through, as a person thinks about it.
//
// The pipeline has eight stages (research, creative, storyboard, scenes,
// visuals, audio, run, produce). That vocabulary is the right one for an
// operator debugging a run and the wrong one for someone asking "where am
// I?". This groups them, and derives progress from the artefacts on disk
// rather than from PipelineRun rows - a stage run from the CLI leaves no row,
// but it does leave its output, and the artefact is what the gate trusts too.
//
// Presentation only: it computes no verdict and blocks nothing.
import type { ProjectAssets } from "../types/assets";
import type { StatusReport } from "../types/project";
import type { LatestRuns } from "../types/pipeline";
import { isActive } from "../types/pipeline";

export type PhaseId = "direction" | "visuals" | "audio" | "assemble" | "review";
export type PhaseState = "done" | "active" | "todo" | "blocked";

export interface Phase {
  id: PhaseId;
  label: string;
  /** What this phase produces, in one line. */
  summary: string;
  state: PhaseState;
  /** The pipeline stages that make it up, for the technical disclosure. */
  stages: string[];
}

const SHAPE: { id: PhaseId; label: string; summary: string; stages: string[] }[] = [
  { id: "direction", label: "Direction", summary: "What this video is, and the plan for it", stages: ["research", "creative", "storyboard"] },
  { id: "visuals", label: "Visuals", summary: "The imagery each scene is built from", stages: ["scenes", "visuals"] },
  { id: "audio", label: "Audio", summary: "The composed and mastered soundtrack", stages: ["audio"] },
  { id: "assemble", label: "Assemble", summary: "The edit, the render and its quality checks", stages: ["run"] },
  { id: "review", label: "Review", summary: "Your decision on the finished video", stages: [] },
];

function stageActive(runs: LatestRuns | undefined, stages: string[]): boolean {
  if (!runs) return false;
  // `produce` drives every phase at once; attribute it to whichever phase
  // is the first unfinished one, which the caller decides, not here.
  return stages.some((s) => {
    const run = (runs as Record<string, unknown>)[s];
    return !!run && isActive(run as never);
  });
}

export interface ProductionProgress {
  phases: Phase[];
  /** The phase the operator is in: the first that is not done. */
  current: Phase;
  /** True when any stage is in flight. */
  busy: boolean;
}

export function deriveProgress(
  assets: ProjectAssets | undefined,
  status: StatusReport | undefined,
  runs: LatestRuns | undefined,
): ProductionProgress {
  const done: Record<PhaseId, boolean> = {
    direction: !!assets?.storyboard || !!assets?.visual_plan,
    visuals: (assets?.images.length ?? 0) > 0,
    audio: !!assets?.audio,
    assemble: !!assets?.video && status?.verdict !== "DRAFT",
    review: status?.verdict === "APPROVED",
  };

  const firstUnfinished = SHAPE.find((p) => !done[p.id]) ?? SHAPE[SHAPE.length - 1];
  const produceActive = !!runs?.produce && isActive(runs.produce);

  const phases: Phase[] = SHAPE.map((shape) => {
    let state: PhaseState = done[shape.id] ? "done" : "todo";
    if (!done[shape.id] && (stageActive(runs, shape.stages) || (produceActive && shape.id === firstUnfinished.id))) {
      state = "active";
    }
    // Only the phase you are actually in can be "blocked" - a gate blocker
    // about audio does not mean Direction went wrong.
    if (shape.id === firstUnfinished.id && (status?.blocking.length ?? 0) > 0 && state !== "active") {
      state = "blocked";
    }
    return { ...shape, state };
  });

  return {
    phases,
    current: phases.find((p) => p.id === firstUnfinished.id)!,
    busy: produceActive || SHAPE.some((p) => stageActive(runs, p.stages)),
  };
}

/** The single most useful thing to do next, given where the project is. */
export interface NextAction {
  label: string;
  /** A stage trigger, when the next step is to run something. */
  stage?: "produce" | "research" | "creative" | "storyboard" | "scenes" | "audio" | "visuals" | "run";
  /** A place to go, when the next step needs a person rather than a run. */
  to?: string;
  why: string;
}

export function nextAction(
  progress: ProductionProgress,
  status: StatusReport | undefined,
  videoId: string,
): NextAction | null {
  if (progress.busy) return null;

  if (status?.verdict === "READY_FOR_REVIEW") {
    return {
      label: "Review and decide",
      to: `/review?project=${videoId}`,
      why: "Everything the pipeline can check has passed. The decision is yours.",
    };
  }

  if ((status?.blocking.length ?? 0) > 0) {
    return {
      label: "Resolve what's blocking",
      to: `/projects/${videoId}#blockers`,
      why: `${status!.blocking.length} thing${status!.blocking.length === 1 ? "" : "s"} must be settled before this can go to review.`,
    };
  }

  switch (progress.current.id) {
    case "direction":
      return { label: "Develop the direction", stage: "produce", why: "Research and creative direction come first — everything downstream derives from them." };
    case "visuals":
      return { label: "Make the visuals", stage: "scenes", why: "Each scene needs an image before the edit can be assembled." };
    case "audio":
      return { label: "Compose the audio", stage: "audio", why: "The soundtrack is composed and mastered from the audio plan." };
    case "assemble":
      return { label: "Assemble and render", stage: "run", why: "Build the edit, render it and run the quality checks." };
    default:
      return null;
  }
}

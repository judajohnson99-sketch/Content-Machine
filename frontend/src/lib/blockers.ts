// Turns a gate blocker (scripts/project.py gate_blockers()) into something a
// person can act on.
//
// The server's string is never discarded or rewritten - it stays on every
// entry as `detail` and is what the UI shows when a blocker does not match
// anything known here. This is presentation only: it cannot clear a blocker,
// change what blocks, or decide a verdict.

export interface ReadableBlocker {
  /** A plain sentence naming the problem. */
  title: string;
  /** The server's own words, verbatim. */
  detail: string;
  /** What resolves it, when there is a single obvious answer. */
  remedy?: string;
  /** Where in the app that remedy lives. */
  action?: { label: string; to: string };
}

type Rule = {
  match: (raw: string) => boolean;
  read: (raw: string, videoId: string) => Omit<ReadableBlocker, "detail">;
};

const RULES: Rule[] = [
  {
    match: (r) => r.startsWith("provenance.images.production_grade is not set"),
    read: (_r, id) => ({
      title: "Nobody has judged the visuals yet",
      remedy:
        "Look at the imagery and record whether it is production-grade. An absent verdict blocks review on purpose — “nobody decided” is not “no objection”.",
      action: { label: "Grade the visuals", to: `/review?project=${id}` },
    }),
  },
  {
    match: (r) => r.startsWith("visuals are not production-grade"),
    read: () => ({
      title: "The visuals were judged not production-grade",
      remedy: "Replace or regenerate the imagery, then record a new verdict.",
    }),
  },
  {
    match: (r) => r.includes("requires depicted imagery"),
    read: () => ({
      title: "This concept needs real depicted imagery, and these are abstract plates",
      remedy:
        "Abstract plates are generated locally when no image provider is available. Bring the GPU machine online (or enable an image provider) and regenerate the scenes.",
    }),
  },
  {
    match: (r) => r.includes("is not present in experiments/concepts.json"),
    read: () => ({
      title: "The concept this production was created from no longer exists",
      remedy: "Restore the concept, or relink this project to one that exists.",
    }),
  },
  {
    match: (r) => r.startsWith("audio not cleared for commercial use"),
    read: (_r, id) => ({
      title: "The audio's rights are not established",
      remedy:
        "Record where the track came from and its licence terms. Audio whose rights cannot be established is refused, never silently used.",
      action: { label: "Open the media library", to: `/media?project=${id}` },
    }),
  },
  {
    match: (r) => r.startsWith("audio is not production-grade"),
    read: () => ({
      title: "The audio was judged not production-grade",
      remedy: "Adjust the mix or swap the source, re-compose, then record a new verdict.",
    }),
  },
  {
    match: (r) => r.includes("a human has to listen and record the verdict"),
    read: (_r, id) => ({
      title: "Someone has to listen to the audio and record a verdict",
      remedy:
        "A synthesised source is standing in for something no automated check can judge. Listen to it, then record whether it is production-grade.",
      action: { label: "Grade the audio", to: `/review?project=${id}` },
    }),
  },
  {
    match: (r) => r.startsWith("QC failed"),
    read: (_r, id) => ({
      title: "The render failed its quality checks",
      remedy: "Fix the cause and render again. The failing checks are listed in the deliverable panel.",
      action: { label: "Open the deliverable", to: `/projects/${id}` },
    }),
  },
  {
    match: (r) => r === "no title set" || r.startsWith("no title"),
    read: () => ({
      title: "The video has no title",
      remedy: "Run the creative stage, or set a title before publishing.",
    }),
  },
  {
    match: (r) => r.startsWith("no description"),
    read: () => ({
      title: "The video has no description",
      remedy: "Run the creative stage to write one.",
    }),
  },
  {
    match: (r) => r.startsWith("no thumbnail"),
    read: () => ({
      title: "No thumbnail has been extracted",
      remedy: "Render the deliverable — thumbnail candidates are pulled from the finished video.",
    }),
  },
];

export function readBlocker(raw: string, videoId: string): ReadableBlocker {
  const rule = RULES.find((r) => r.match(raw));
  // Unknown blockers pass through verbatim rather than being swallowed: the
  // gate may grow a rule this table has never heard of, and silence would
  // be worse than an unfriendly sentence.
  if (!rule) return { title: raw, detail: raw };
  return { ...rule.read(raw, videoId), detail: raw };
}

export function readBlockers(raw: string[], videoId: string): ReadableBlocker[] {
  return raw.map((r) => readBlocker(r, videoId));
}

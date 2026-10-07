import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { createFromGoal, type GoalResult } from "../../api/concepts";
import { triggerStage } from "../../api/pipeline";
import { ApiError } from "../../api/client";
import { newClientRequestId } from "../../lib/clientRequestId";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import styles from "./GoalComposer.module.css";

const EXCERPT_SECONDS = 90;

const EXAMPLES = [
  "Create a 2-hour psychedelic Dreamdrip sleep experience with rain and ambient music",
  "A 30-minute rainy-window study session with warm lamplight and no narration",
  "A one-hour winter forest night for deep sleep, wind and distant owls",
];

// The dashboard's front door. An operator says what they want in a sentence;
// the server derives the concept, scaffolds the project and writes the
// research brief every production carries, then - unless they say otherwise -
// starts the run. Nothing here interprets the goal: that is scripts/goal.py,
// and what it understood is shown back before the run starts.
export function GoalComposer() {
  const navigate = useNavigate();
  const [goal, setGoal] = useState("");
  const [minutes, setMinutes] = useState("");
  const [preview, setPreview] = useState(true);
  const [startNow, setStartNow] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [derived, setDerived] = useState<GoalResult | null>(null);

  const mutation = useMutation({
    mutationFn: async (): Promise<GoalResult> => {
      // The excerpt is a view of the full production, not a different one:
      // the full length still comes from the goal (or the field), and the
      // excerpt only shortens what gets built first, so the workspace can
      // offer to produce it properly once the look has been judged.
      const result = await createFromGoal({
        goal: goal.trim(),
        minutes: minutes.trim() ? Number(minutes) : null,
        excerpt_seconds: preview ? EXCERPT_SECONDS : null,
      });
      setDerived(result);
      if (startNow) {
        await triggerStage(result.project.video_id, "produce", newClientRequestId(), {});
      }
      return result;
    },
    onSuccess: (result) => navigate(`/projects/${result.project.video_id}`),
    onError: (e: unknown) => {
      if (e instanceof ApiError && e.status === 409) {
        setError("A production with that id already exists. Try again - a new id is generated each time.");
      } else {
        setError(e instanceof Error ? e.message : "Request failed.");
      }
    },
  });

  const valid = goal.trim().length >= 8;

  return (
    <Card className={styles.card}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          mutation.mutate();
        }}
      >
        <h2 className={styles.title}>What do you want to make?</h2>
        <p className={styles.sub}>
          Describe it the way you would to a person. Length, mood, imagery and
          sound are all read from your words - say as much or as little as you
          like.
        </p>

        <textarea
          className={styles.goal}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder={EXAMPLES[0]}
          rows={3}
          aria-label="Production goal"
        />

        <div className={styles.examples}>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              className={styles.example}
              onClick={() => setGoal(example)}
            >
              {example}
            </button>
          ))}
        </div>

        <div className={styles.options}>
          <label className={`${styles.toggle} ${preview ? styles.toggleOn : ""}`}>
            <input
              type="checkbox"
              checked={preview}
              onChange={(e) => setPreview(e.target.checked)}
            />
            <span>
              <span className={styles.toggleTitle}>Start with a {EXCERPT_SECONDS}-second excerpt</span>
              <span className={styles.toggleSub}>
                Same research, same direction, same edit - short enough to judge
                before committing to the full length.
              </span>
            </span>
          </label>

          <label className={styles.field}>
            <span className={styles.label}>Full length (minutes)</span>
            <input
              className={styles.input}
              type="number"
              min={0.5}
              step="any"
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              placeholder="read from your words"
            />
          </label>

          <label className={styles.checkbox}>
            <input
              type="checkbox"
              checked={startNow}
              onChange={(e) => setStartNow(e.target.checked)}
            />
            Research and produce immediately
          </label>
        </div>

        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}

        {derived && (
          <div className={styles.derived}>
            <Badge tone="success">understood as</Badge>
            <span>{derived.plan.title_pattern}</span>
          </div>
        )}

        <div className={styles.actions}>
          <Button type="submit" variant="primary" size="lg" disabled={!valid || mutation.isPending}>
            {mutation.isPending ? "Working it out…" : startNow ? "Create & produce" : "Create production"}
          </Button>
          <span className={styles.hint}>
            Takes a few seconds: the goal is turned into a concept, a project and
            a research brief before anything runs.
          </span>
        </div>
      </form>
    </Card>
  );
}

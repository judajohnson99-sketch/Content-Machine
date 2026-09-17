import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { triggerStage } from "../api/pipeline";
import { newClientRequestId } from "../lib/clientRequestId";
import { Button } from "./ui/Button";
import type { StageName } from "../types/pipeline";
import styles from "./StageActionButton.module.css";

interface Props {
  videoId: string;
  stage: StageName;
  label: string;
  params?: Record<string, unknown>;
  disabled?: boolean;
  disabledReason?: string;
}

// The one reusable control every pipeline stage (and Produce) triggers
// through - it only ever calls the existing stage-trigger endpoint and
// reports what the API said. It never decides whether an action is valid;
// that is scripts.project's job, surfaced through the PipelineRun the
// backend returns (or, for a 409, apps.engine.exceptions.ProjectBusy).
export function StageActionButton({ videoId, stage, label, params, disabled, disabledReason }: Props) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<{ kind: "busy" | "error"; message: string } | null>(null);

  const mutation = useMutation({
    mutationFn: () => triggerStage(videoId, stage, newClientRequestId(), params),
    onSuccess: () => {
      setNotice(null);
      queryClient.invalidateQueries({ queryKey: ["pipeline-runs", videoId] });
      queryClient.invalidateQueries({ queryKey: ["project-status", videoId] });
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError && error.status === 409) {
        setNotice({
          kind: "busy",
          message: "Another operation is already in progress for this project - try again shortly.",
        });
        // The poll interval will catch up on its own, but refetching now
        // means the busy state (and whichever run is actually active)
        // shows up immediately instead of after the next tick.
        queryClient.invalidateQueries({ queryKey: ["pipeline-runs", videoId] });
      } else {
        setNotice({
          kind: "error",
          message: error instanceof Error ? error.message : "Request failed.",
        });
      }
    },
  });

  const isDisabled = disabled || mutation.isPending;

  return (
    <div className={styles.wrap}>
      <Button
        variant="primary"
        size="sm"
        onClick={() => {
          setNotice(null);
          mutation.mutate();
        }}
        disabled={isDisabled}
        title={isDisabled ? disabledReason : undefined}
      >
        {mutation.isPending ? "Starting…" : label}
      </Button>
      {notice && (
        <p role="alert" className={`${styles.notice} ${notice.kind === "busy" ? styles.busy : styles.error}`}>
          {notice.message}
        </p>
      )}
    </div>
  );
}

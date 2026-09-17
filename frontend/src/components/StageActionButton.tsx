import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { triggerStage } from "../api/pipeline";
import { newClientRequestId } from "../lib/clientRequestId";
import type { StageName } from "../types/pipeline";

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
    <div>
      <button
        type="button"
        onClick={() => {
          setNotice(null);
          mutation.mutate();
        }}
        disabled={isDisabled}
        title={isDisabled ? disabledReason : undefined}
        style={{
          padding: "6px 14px",
          borderRadius: 6,
          border: "1px solid var(--border)",
          background: isDisabled ? "var(--code-bg)" : "var(--accent)",
          color: isDisabled ? "var(--text)" : "#fff",
          cursor: isDisabled ? "not-allowed" : "pointer",
          fontSize: 13,
          fontWeight: 600,
        }}
      >
        {mutation.isPending ? "Starting…" : label}
      </button>
      {notice && (
        <p
          role="alert"
          style={{
            margin: "4px 0 0",
            fontSize: 12,
            color: notice.kind === "busy" ? "#9a6700" : "#cf222e",
            maxWidth: 240,
          }}
        >
          {notice.message}
        </p>
      )}
    </div>
  );
}

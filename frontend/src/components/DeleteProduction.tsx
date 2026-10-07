import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { deleteProject } from "../api/projects";
import { Button } from "./ui/Button";
import styles from "./DeleteProduction.module.css";

interface Props {
  videoId: string;
  disabled?: boolean;
  disabledReason?: string;
}

// Deleting a production is the one irreversible thing the dashboard can do,
// so it is deliberately not a one-click row action: the operator opens it,
// types the production's id, and only then can the button fire. The server
// requires the same echo and refuses a published project outright - this is
// the second lock on that door, not the only one.
export function DeleteProduction({ videoId, disabled, disabledReason }: Props) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => deleteProject(videoId, reason.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      navigate("/");
    },
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : "Deletion failed."),
  });

  if (!open) {
    return (
      <div className={styles.wrap}>
        <button
          type="button"
          className={styles.reveal}
          onClick={() => setOpen(true)}
          disabled={disabled}
          title={disabled ? disabledReason : undefined}
        >
          Delete this production…
        </button>
        <p className={styles.hint}>
          Removes the project directory, its research and the concept derived
          for it. There is no undo.
        </p>
      </div>
    );
  }

  return (
    <form
      className={styles.wrap}
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        mutation.mutate();
      }}
    >
      <p className={styles.warning}>
        This permanently removes <strong>{videoId}</strong>: its renders,
        images, audio, logs, research brief and findings. Type the id to
        confirm.
      </p>
      <input
        className={styles.input}
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        placeholder={videoId}
        aria-label="Type the production id to confirm deletion"
        spellCheck={false}
        autoFocus
      />
      <input
        className={styles.input}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Why (recorded in the deletion ledger)"
        aria-label="Reason for deletion"
      />
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <Button
          type="submit"
          variant="danger"
          size="sm"
          disabled={typed.trim() !== videoId || mutation.isPending}
        >
          {mutation.isPending ? "Deleting…" : "Delete permanently"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            setOpen(false);
            setTyped("");
            setError(null);
          }}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}

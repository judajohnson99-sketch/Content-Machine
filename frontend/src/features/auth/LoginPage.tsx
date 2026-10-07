import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { login, type Identity } from "../../api/auth";
import { ApiError } from "../../api/client";
import { Button } from "../../components/ui/Button";
import styles from "./LoginPage.module.css";

/**
 * The app's sign-in screen.
 *
 * It exists so that "logged out" is a state inside the product rather than
 * a dead end that tells the reader to go and use Django's admin instead.
 */
export function LoginPage({ onSignedIn }: { onSignedIn: (who: Identity) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: () => login(username, password),
    onSuccess: onSignedIn,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (username && password) mutation.mutate();
  }

  const message =
    mutation.error instanceof ApiError
      ? mutation.error.message
      : mutation.error
        ? "Could not reach the control center."
        : null;

  return (
    <div className={styles.page}>
      <form className={styles.panel} onSubmit={submit}>
        <div className={styles.mark}>
          <span className={styles.dot} aria-hidden="true" />
          <h1 className={styles.title}>Content Machine</h1>
        </div>
        <p className={styles.subtitle}>Sign in to your control center.</p>

        {message && (
          <p className={styles.error} role="alert">
            {message}
          </p>
        )}

        <label className={styles.field}>
          <span className={styles.label}>Username</span>
          <input
            className={styles.input}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </label>

        <label className={styles.field}>
          <span className={styles.label}>Password</span>
          <input
            className={styles.input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        <Button
          className={styles.submit}
          type="submit"
          variant="primary"
          size="lg"
          disabled={mutation.isPending || !username || !password}
        >
          {mutation.isPending ? "Signing in…" : "Sign in"}
        </Button>

        <p className={styles.note}>
          This is a single-owner control center. Your sign-in also identifies you as
          the reviewer on every approval you record.
        </p>
      </form>
    </div>
  );
}

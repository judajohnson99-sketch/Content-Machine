import type { ReactNode } from "react";
import type { Tone } from "../../lib/statusTokens";
import styles from "./Badge.module.css";

interface Props {
  tone: Tone;
  children: ReactNode;
  pulse?: boolean;
}

// The one place a status tone becomes pixels - a colored dot + label pill.
// Presentation only: it never interprets what a status means.
export function Badge({ tone, children, pulse }: Props) {
  const className = [styles.badge, styles[tone], pulse ? styles.pulse : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={className}>
      <span className={styles.dot} aria-hidden="true" />
      {children}
    </span>
  );
}

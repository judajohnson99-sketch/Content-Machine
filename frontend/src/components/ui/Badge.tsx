import type { ReactNode } from "react";
import type { Tone } from "../../lib/statusTokens";
import styles from "./Badge.module.css";

interface Props {
  tone: Tone;
  children: ReactNode;
  pulse?: boolean;
  title?: string;
}

// The one place a status tone becomes pixels - a coloured dot + label pill.
// Presentation only: it never interprets what a status means.
export function Badge({ tone, children, pulse, title }: Props) {
  const className = [styles.badge, styles[tone], pulse ? styles.pulse : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={className} title={title}>
      <span className={styles.dot} aria-hidden="true" />
      {children}
    </span>
  );
}

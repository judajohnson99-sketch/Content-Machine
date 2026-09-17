import type { ReactNode } from "react";
import styles from "./States.module.css";
import { AlertTriangleIcon, InboxIcon } from "./icons";

interface StateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  compact?: boolean;
}

export function EmptyState({ icon, title, description, action, compact }: StateProps) {
  return (
    <div className={`${styles.state} ${compact ? styles.compact : ""}`}>
      <div className={styles.icon}>{icon ?? <InboxIcon width={24} height={24} />}</div>
      <p className={styles.title}>{title}</p>
      {description && <p className={styles.description}>{description}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}

interface ErrorProps extends StateProps {
  // Where it failed (a stage, an endpoint, a worker) - short, monospace.
  where?: string;
  // The likely reason and what the operator can do about it.
  hint?: string;
  // Raw technical detail, collapsed by default.
  detail?: string;
}

// Every failure surface answers the same four questions: what failed,
// where, why (likely), and what to do - with the raw detail one click away
// rather than in the operator's face.
export function ErrorState({ title, description, where, hint, detail, action, compact }: ErrorProps) {
  return (
    <div className={`${styles.state} ${compact ? styles.compact : ""}`} role="alert">
      <div className={`${styles.icon} ${styles.iconDanger}`}>
        <AlertTriangleIcon width={24} height={24} />
      </div>
      <p className={styles.errorTitle}>{title}</p>
      {where && <p className={styles.where}>{where}</p>}
      {description && <p className={styles.description}>{description}</p>}
      {hint && <p className={styles.hint}>{hint}</p>}
      {action && <div className={styles.action}>{action}</div>}
      {detail && (
        <details className={styles.details}>
          <summary>Technical detail</summary>
          <pre>{detail}</pre>
        </details>
      )}
    </div>
  );
}

// A few skeleton rows, for content that's known to be a list/table while it
// loads - avoids a layout jump once data arrives.
export function SkeletonRows({ count = 4 }: { count?: number }) {
  return (
    <div className={styles.skeletonGroup} aria-hidden="true">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className={styles.skeletonRow} style={{ width: `${88 - i * 6}%` }} />
      ))}
    </div>
  );
}

export function InlineSpinner({ label }: { label: string }) {
  return (
    <span className={styles.inlineSpinner} role="status">
      <span className={styles.spinner} />
      {label}
    </span>
  );
}

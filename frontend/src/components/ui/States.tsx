import type { ReactNode } from "react";
import styles from "./States.module.css";
import { AlertTriangleIcon, InboxIcon } from "./icons";

interface StateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ icon, title, description, action }: StateProps) {
  return (
    <div className={styles.state}>
      <div className={styles.icon}>{icon ?? <InboxIcon width={28} height={28} />}</div>
      <p className={styles.title}>{title}</p>
      {description && <p className={styles.description}>{description}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}

export function ErrorState({ title, description, action }: StateProps) {
  return (
    <div className={styles.state} role="alert">
      <div className={styles.icon}>
        <AlertTriangleIcon width={28} height={28} />
      </div>
      <p className={styles.errorTitle}>{title}</p>
      {description && <p className={styles.description}>{description}</p>}
      {action && <div className={styles.action}>{action}</div>}
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

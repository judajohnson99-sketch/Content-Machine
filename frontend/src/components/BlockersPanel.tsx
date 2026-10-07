import { Link } from "react-router-dom";
import { readBlockers } from "../lib/blockers";
import { CheckCircleIcon } from "./ui/icons";
import styles from "./BlockersPanel.module.css";

/**
 * What stands between this production and a review decision, in sentences.
 *
 * The gate's own strings are preserved verbatim under each item so nothing
 * is lost in translation, and an unrecognised blocker still shows in full.
 */
export function BlockersPanel({ videoId, blocking }: { videoId: string; blocking: string[] }) {
  if (blocking.length === 0) {
    return (
      <p className={styles.clear}>
        <CheckCircleIcon width={16} height={16} /> Nothing is blocking this production.
      </p>
    );
  }

  const readable = readBlockers(blocking, videoId);

  return (
    <ul className={styles.list}>
      {readable.map((item) => (
        <li key={item.detail} className={styles.item}>
          <p className={styles.title}>{item.title}</p>
          {item.remedy && <p className={styles.remedy}>{item.remedy}</p>}
          {item.action && (
            <Link className={styles.action} to={item.action.to}>
              {item.action.label} →
            </Link>
          )}
          {item.title !== item.detail && (
            <details className={styles.raw}>
              <summary>What the gate says</summary>
              <p className={styles.rawText}>{item.detail}</p>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}

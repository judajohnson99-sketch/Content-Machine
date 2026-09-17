import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import styles from "./PageHeader.module.css";
import { ArrowLeftIcon } from "./icons";

interface Props {
  title: ReactNode;
  description?: ReactNode;
  backTo?: { to: string; label: string };
  actions?: ReactNode;
}

export function PageHeader({ title, description, backTo, actions }: Props) {
  return (
    <div className={styles.header}>
      <div>
        {backTo && (
          <Link to={backTo.to} className={styles.back}>
            <ArrowLeftIcon width={14} height={14} />
            {backTo.label}
          </Link>
        )}
        <h1 className={styles.title}>{title}</h1>
        {description && <p className={styles.description}>{description}</p>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  );
}

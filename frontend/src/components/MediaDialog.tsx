import { useEffect, useRef, type ReactNode } from "react";
import styles from "./MediaDialog.module.css";

export function MediaDialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (typeof dialog.current?.showModal === "function") dialog.current.showModal();
    else dialog.current?.setAttribute("open", "");
  }, []);
  return <dialog ref={dialog} className={styles.dialog} aria-label={title} onCancel={onClose} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className={styles.bar}><h2>{title}</h2><button type="button" onClick={onClose} aria-label="Close preview" autoFocus>×</button></div>
    <div className={styles.media}>{children}</div>
  </dialog>;
}

import styles from "./StatCard.module.css";

interface Props {
  label: string;
  value: number;
  tone?: "default" | "accent" | "success" | "warning" | "danger";
}

export function StatCard({ label, value, tone = "default" }: Props) {
  const valueClass = tone === "default" ? styles.value : `${styles.value} ${styles[tone]}`;
  return (
    <div className={styles.card}>
      <span className={styles.label}>{label}</span>
      <span className={valueClass}>{value}</span>
    </div>
  );
}

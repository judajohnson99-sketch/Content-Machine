import { useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import styles from "./AppShell.module.css";
import { CheckCircleIcon, CpuIcon, GridIcon, BookIcon } from "../ui/icons";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
}

const PRIMARY_NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: <GridIcon /> },
  { to: "/review", label: "Review Center", icon: <CheckCircleIcon /> },
];

// Not routed yet - the backend has no Worker/Knowledge web surface (see
// scripts/worker.py and the knowledge layer, both CLI-only today). Listed
// so the operator can see the control center's intended shape without the
// UI pretending those pages exist.
const UPCOMING_NAV: NavItem[] = [
  { to: "/worker", label: "Worker", icon: <CpuIcon /> },
  { to: "/knowledge", label: "Knowledge", icon: <BookIcon /> },
];

export function AppShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className={styles.shell}>
      <div className={styles.topbar}>
        <button
          type="button"
          className={styles.menuButton}
          onClick={() => setMenuOpen(true)}
          aria-label="Open navigation menu"
          aria-expanded={menuOpen}
        >
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
            <path d="M3 5.5h14M3 10h14M3 14.5h14" />
          </svg>
        </button>
        <BrandMark compact />
      </div>

      {menuOpen && (
        <div className={styles.backdrop} onClick={() => setMenuOpen(false)} aria-hidden="true" />
      )}

      <nav
        className={`${styles.sidebar} ${menuOpen ? styles.sidebarOpen : ""}`}
        aria-label="Primary"
      >
        <BrandMark onNavigate={() => setMenuOpen(false)} />
        <div className={styles.navGroup}>
          {PRIMARY_NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) => `${styles.navItem} ${isActive ? styles.navItemActive : ""}`}
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </div>

        <div className={styles.navLabel}>Coming soon</div>
        <div className={styles.navGroup}>
          {UPCOMING_NAV.map((item) => (
            <span key={item.to} className={styles.navItemDisabled} aria-disabled="true">
              {item.icon}
              {item.label}
              <span className={styles.navBadge}>Soon</span>
            </span>
          ))}
        </div>

        <div className={styles.spacer} />
      </nav>

      <div className={styles.main}>
        <div className={styles.content}>{children}</div>
      </div>
    </div>
  );
}

function BrandMark({ compact = false, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  return (
    <NavLink to="/" className={styles.brand} style={{ padding: compact ? 0 : undefined }} onClick={onNavigate}>
      <span className={styles.brandMark} aria-hidden="true" />
      {!compact && (
        <span>
          <div className={styles.brandName}>Content Machine</div>
          <div className={styles.brandSub}>Control Center</div>
        </span>
      )}
      {compact && <span className={styles.brandName}>Content Machine</span>}
    </NavLink>
  );
}

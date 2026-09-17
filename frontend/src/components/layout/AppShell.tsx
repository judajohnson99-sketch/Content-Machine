import { useEffect, useState, type ReactNode } from "react";
import { NavLink, matchPath, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import styles from "./AppShell.module.css";
import { getReadiness, listRecentRuns } from "../../api/system";
import { listProjects } from "../../api/projects";
import { gpuStateLabel, statusTone } from "../../lib/statusTokens";
import { isReviewable } from "../../lib/projectStatus";
import { Badge } from "../ui/Badge";
import {
  ActivityIcon, CheckCircleIcon, CollapseIcon, GpuIcon, GridIcon, PlayIcon, SearchIcon, WaveIcon, XIcon,
} from "../ui/icons";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  count?: number;
}

const COLLAPSE_KEY = "cm.sidebar.collapsed";

export function AppShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const location = useLocation();
  const projectMatch = matchPath("/projects/:videoId", location.pathname);
  const currentProject =
    projectMatch?.params.videoId && projectMatch.params.videoId !== "new" ? projectMatch.params.videoId : null;

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    } catch {
      /* private mode: preference is not persisted, that's fine */
    }
  }, [collapsed]);

  // Close the mobile drawer on navigation.
  useEffect(() => setMenuOpen(false), [location.pathname]);

  const readiness = useQuery({ queryKey: ["readiness"], queryFn: getReadiness, refetchInterval: 30_000 });
  const activity = useQuery({ queryKey: ["recent-runs"], queryFn: listRecentRuns, refetchInterval: 10_000 });
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects, refetchInterval: 20_000 });

  const activeCount = activity.data?.active.length ?? 0;
  const reviewCount = (projects.data ?? []).filter((p) => isReviewable(p.overall_status)).length;

  const nav: NavItem[] = [
    { to: "/", label: "Dashboard", icon: <GridIcon /> },
    { to: "/projects/new", label: "New Production", icon: <PlayIcon /> },
    { to: "/review", label: "Review Center", icon: <CheckCircleIcon />, count: reviewCount },
  ];

  const gpu = readiness.data?.depicted_imagery;
  const gpuTone = gpu ? statusTone(gpu.state) : "neutral";

  return (
    <div className={`${styles.shell} ${collapsed ? styles.shellCollapsed : ""}`}>
      <header className={styles.topbar}>
        <button
          type="button"
          className={styles.menuButton}
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
          aria-expanded={menuOpen}
        >
          {menuOpen ? <XIcon /> : (
            <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
              <path d="M3 5.5h14M3 10h14M3 14.5h14" />
            </svg>
          )}
        </button>
        <BrandMark compact />
        {gpu && (
          <span className={styles.topbarStatus}>
            <Badge tone={gpuTone} title={gpu.detail}>{gpuStateLabel(gpu.state)}</Badge>
          </span>
        )}
      </header>

      {menuOpen && <div className={styles.backdrop} onClick={() => setMenuOpen(false)} aria-hidden="true" />}

      <nav
        className={`${styles.sidebar} ${menuOpen ? styles.sidebarOpen : ""}`}
        aria-label="Primary"
      >
        <div className={styles.sidebarTop}>
          <BrandMark collapsed={collapsed} />
          <button
            type="button"
            className={styles.collapseButton}
            onClick={() => setCollapsed((v) => !v)}
            aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
            title={collapsed ? "Expand navigation" : "Collapse navigation"}
          >
            <CollapseIcon />
          </button>
        </div>

        <div className={styles.navGroup}>
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              title={collapsed ? item.label : undefined}
              className={({ isActive }) => `${styles.navItem} ${isActive ? styles.navItemActive : ""}`}
            >
              <span className={styles.navIcon}>{item.icon}</span>
              <span className={styles.navLabel}>{item.label}</span>
              {item.count ? <span className={styles.navCount}>{item.count}</span> : null}
            </NavLink>
          ))}
        </div>

        {currentProject && (
          <div className={styles.context}>
            <div className={styles.contextLabel}>Open project</div>
            <NavLink to={`/projects/${currentProject}`} className={styles.contextItem} title={currentProject}>
              <span className={styles.contextDot} aria-hidden="true" />
              <span className={styles.contextName}>{currentProject}</span>
            </NavLink>
          </div>
        )}

        <div className={styles.spacer} />

        <section className={styles.systemPanel} aria-label="System status">
          <div className={styles.systemHead}>
            <span className={styles.systemTitle}>System</span>
            {activeCount > 0 && (
              <span className={styles.activePill} title={`${activeCount} pipeline job(s) running`}>
                <ActivityIcon width={12} height={12} /> {activeCount}
              </span>
            )}
          </div>
          <ul className={styles.systemList}>
            <li className={styles.systemRow} title={gpu?.detail ?? "Loading readiness…"}>
              <span className={`${styles.systemIcon} ${styles[`tone_${gpuTone}`]}`}><GpuIcon width={15} height={15} /></span>
              <span className={styles.systemText}>
                <span className={styles.systemName}>Depicted imagery</span>
                <span className={styles.systemValue}>{gpu ? gpuStateLabel(gpu.state) : "…"}</span>
              </span>
            </li>
            <li className={styles.systemRow} title={readiness.data?.remote_gpu.detail ?? ""}>
              <span className={`${styles.systemIcon} ${styles[`tone_${readiness.data?.remote_gpu.queue.running ? "info" : "neutral"}`]}`}>
                <ActivityIcon width={15} height={15} />
              </span>
              <span className={styles.systemText}>
                <span className={styles.systemName}>GPU queue</span>
                <span className={styles.systemValue}>
                  {readiness.data
                    ? `${readiness.data.remote_gpu.queue.running} rendering · ${readiness.data.remote_gpu.queue.queued} waiting`
                    : "…"}
                </span>
              </span>
            </li>
            <li className={styles.systemRow}>
              <span className={`${styles.systemIcon} ${styles[`tone_${readiness.data?.search_available ? "success" : "neutral"}`]}`}>
                <SearchIcon width={15} height={15} />
              </span>
              <span className={styles.systemText}>
                <span className={styles.systemName}>Research</span>
                <span className={styles.systemValue}>
                  {readiness.data ? (readiness.data.search_available ? readiness.data.search_provider : "no provider") : "…"}
                </span>
              </span>
            </li>
            <li className={styles.systemRow}>
              <span className={`${styles.systemIcon} ${styles.tone_success}`}><WaveIcon width={15} height={15} /></span>
              <span className={styles.systemText}>
                <span className={styles.systemName}>Audio &amp; narration</span>
                <span className={styles.systemValue}>synth · piper</span>
              </span>
            </li>
          </ul>
        </section>
      </nav>

      <div className={styles.main}>
        <div className={styles.content}>{children}</div>
      </div>
    </div>
  );
}

function BrandMark({ compact = false, collapsed = false }: { compact?: boolean; collapsed?: boolean }) {
  return (
    <NavLink to="/" className={styles.brand} title="Content Machine">
      <span className={styles.brandMark} aria-hidden="true">
        <span className={styles.brandCore} />
      </span>
      {!compact && !collapsed && (
        <span className={styles.brandText}>
          <span className={styles.brandName}>Content Machine</span>
          <span className={styles.brandSub}>Control Center</span>
        </span>
      )}
      {compact && <span className={styles.brandName}>Content Machine</span>}
    </NavLink>
  );
}

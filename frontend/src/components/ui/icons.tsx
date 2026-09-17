import type { SVGProps } from "react";

// A small, hand-rolled icon set (no icon library dependency) - stroke-based,
// 20px viewBox, currentColor throughout so they inherit tone from CSS. Only
// the icons the shell/pages actually use; add to this set rather than
// reaching for a new dependency.
type IconProps = SVGProps<SVGSVGElement>;

function base(props: IconProps) {
  return {
    width: 18,
    height: 18,
    viewBox: "0 0 20 20",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    ...props,
  };
}

export function GridIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="3" y="3" width="6" height="6" rx="1.2" />
      <rect x="11" y="3" width="6" height="6" rx="1.2" />
      <rect x="3" y="11" width="6" height="6" rx="1.2" />
      <rect x="11" y="11" width="6" height="6" rx="1.2" />
    </svg>
  );
}

export function CheckCircleIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="10" cy="10" r="7" />
      <path d="m7 10 2 2 4-4.5" />
    </svg>
  );
}

export function LayersIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="m10 3 7 3.5-7 3.5-7-3.5Z" />
      <path d="m3 10.5 7 3.5 7-3.5" />
      <path d="m3 14 7 3.5 7-3.5" />
    </svg>
  );
}

export function CpuIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="6" y="6" width="8" height="8" rx="1.2" />
      <rect x="3" y="3" width="14" height="14" rx="2" />
      <path d="M7.5 1.5v1.8M10 1.5v1.8M12.5 1.5v1.8M7.5 16.7v1.8M10 16.7v1.8M12.5 16.7v1.8M1.5 7.5h1.8M1.5 10h1.8M1.5 12.5h1.8M16.7 7.5h1.8M16.7 10h1.8M16.7 12.5h1.8" />
    </svg>
  );
}

export function BookIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 4.5c0-.6.4-1 1-1h4.5v13H5c-.6 0-1 .4-1 1v-13Z" />
      <path d="M16 4.5c0-.6-.4-1-1-1h-4.5v13H15c.6 0 1 .4 1 1v-13Z" />
    </svg>
  );
}

export function ArrowLeftIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12.5 4.5 6 10l6.5 5.5" />
    </svg>
  );
}

export function ChevronIcon(props: IconProps & { direction?: "right" | "down" }) {
  const { direction = "right", ...rest } = props;
  return (
    <svg {...base(rest)} style={{ transform: direction === "down" ? "rotate(90deg)" : undefined }}>
      <path d="m7.5 4.5 5.5 5.5-5.5 5.5" />
    </svg>
  );
}

export function AlertTriangleIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M10 3 2.5 16h15L10 3Z" />
      <path d="M10 8.2v3.4" />
      <circle cx="10" cy="14.2" r="0.15" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function InboxIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M3 10.5 5.2 4h9.6l2.2 6.5" />
      <path d="M3 10.5v4c0 .8.7 1.5 1.5 1.5h11c.8 0 1.5-.7 1.5-1.5v-4h-4.2l-1 1.8H8.7l-1-1.8H3Z" />
    </svg>
  );
}

export function PlayIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M6 4.2v11.6l9-5.8-9-5.8Z" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function ClockIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 6v4l2.6 2" />
    </svg>
  );
}

export function FolderIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M3 5.5c0-.6.4-1 1-1h3.6l1.4 1.6H16c.6 0 1 .4 1 1v8.4c0 .6-.4 1-1 1H4c-.6 0-1-.4-1-1V5.5Z" />
    </svg>
  );
}

export function ImageIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="3" y="4" width="14" height="12" rx="1.6" />
      <circle cx="7.2" cy="8" r="1.3" />
      <path d="m3.5 14.5 4.2-4 3 2.8 2.3-2.2 3.5 3.4" />
    </svg>
  );
}

export function FilmIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="3" y="4" width="14" height="12" rx="1.6" />
      <path d="M3 8h14M3 12h14M7 4v12M13 4v12" />
    </svg>
  );
}

export function WaveIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M3 10h1.5l1.5-4 2 8 2-9 2 10 2-7 1.5 2H17" />
    </svg>
  );
}

export function GpuIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="2.5" y="5" width="15" height="9" rx="1.6" />
      <circle cx="8" cy="9.5" r="2.2" />
      <path d="M12.5 8h2.5M12.5 11h2.5M5 14v2M9 14v2M13 14v2" />
    </svg>
  );
}

export function ActivityIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M2.5 10h3l2-5 3 10 2.5-7 1.5 2h3" />
    </svg>
  );
}

export function SparkIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M10 3v3.5M10 13.5V17M3 10h3.5M13.5 10H17M5.4 5.4l2.2 2.2M12.4 12.4l2.2 2.2M14.6 5.4l-2.2 2.2M7.6 12.4l-2.2 2.2" />
    </svg>
  );
}

export function EyeIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5Z" />
      <circle cx="10" cy="10" r="2.4" />
    </svg>
  );
}

export function ShieldIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M10 2.8 4 5v4.4c0 3.6 2.6 6.5 6 7.8 3.4-1.3 6-4.2 6-7.8V5l-6-2.2Z" />
      <path d="m7.4 10 1.8 1.8 3.4-3.6" />
    </svg>
  );
}

export function RefreshIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M16 9.5A6 6 0 0 0 5.2 6.4M4 10.5a6 6 0 0 0 10.8 3.1" />
      <path d="M15.5 3.5v3.3h-3.3M4.5 16.5v-3.3h3.3" />
    </svg>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="9" cy="9" r="5.2" />
      <path d="m13 13 4 4" />
    </svg>
  );
}

export function CollapseIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="3" y="3.5" width="14" height="13" rx="1.6" />
      <path d="M8 3.5v13M13.5 8 11.5 10l2 2" />
    </svg>
  );
}

export function XIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="m5 5 10 10M15 5 5 15" />
    </svg>
  );
}

export function InfoIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 9v4.5" />
      <circle cx="10" cy="6.6" r="0.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="m4.5 10.5 3.5 3.5 7.5-8" />
    </svg>
  );
}

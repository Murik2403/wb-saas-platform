// Same stroke-based icon set as the locked mockup (Claude Design canvas
// "MARKETSHELPER Redesign") -- 20px grid, currentColor, stroke-width 1.8.
// Keep new icons in this file in the same style rather than reaching for an
// icon library; this set is small and deliberate on purpose.
import type { SVGProps } from "react";

const base = {
  width: 17,
  height: 17,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function TodayIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M2 12h4l3-8 4 16 3-8h6" />
    </svg>
  );
}

export function OverviewIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="12" width="4" height="8" />
      <rect x="10" y="7" width="4" height="13" />
      <rect x="17" y="3" width="4" height="17" />
    </svg>
  );
}

export function FinanceIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <rect x="2.5" y="6" width="19" height="14" rx="2.5" />
      <path d="M2.5 10.5h19" />
      <circle cx="17.2" cy="15" r="1.15" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function ProductionIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M3 8.5 12 4l9 4.5-9 4.5-9-4.5Z" />
      <path d="M3 13l9 4.5 9-4.5" />
      <path d="M3 8.5v9" />
      <path d="M21 8.5v9" />
    </svg>
  );
}

export function ProcurementIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <circle cx="9" cy="20" r="1.4" />
      <circle cx="18" cy="20" r="1.4" />
      <path d="M2.5 4h2.6l2 12.2h11.2l1.9-8.4H6.2" />
    </svg>
  );
}

export function ProductsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5v-9Z" />
      <path d="M3 7.5 12 12l9-4.5" />
      <path d="M12 12v9" />
    </svg>
  );
}

export function AdsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M3 11 14 4l7 3.2-11 6.6L3 11Z" />
      <path d="M10 13.8v5.4l-3-1.6v-4.2" />
      <path d="M21 7.2v7" />
    </svg>
  );
}

export function StockIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="4" width="18" height="5" rx="1.2" />
      <rect x="4.3" y="10.5" width="15.4" height="9.5" rx="1.2" />
      <path d="M9.5 14.5h5" />
    </svg>
  );
}

export function ControlIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3 20 6v6c0 5-3.4 8-8 9-4.6-1-8-4-8-9V6Z" />
      <path d="M8.7 12.2l2.2 2.2 4.3-4.6" />
    </svg>
  );
}

export function AgentsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <rect x="6" y="6" width="12" height="12" rx="3" />
      <circle cx="9.7" cy="11" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="14.3" cy="11" r="0.9" fill="currentColor" stroke="none" />
      <path d="M9.5 14.4c.7.7 1.6 1 2.5 1s1.8-.3 2.5-1" />
      <path d="M12 3v3" />
      <path d="M4.5 9h1.6M17.9 9h1.6M4.5 15h1.6M17.9 15h1.6" />
    </svg>
  );
}

export function ReportsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M6 2.7h9l4 4v13.6a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3.7a1 1 0 0 1 1-1Z" />
      <path d="M8.5 12h7M8.5 15.4h7M8.5 8.6h4" />
    </svg>
  );
}

export function SettingsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 13.9a1.7 1.7 0 0 0 .3 1.9l.1.1a2.1 2.1 0 1 1-2.9 2.9l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2a2.1 2.1 0 1 1-4.1 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2.1 2.1 0 1 1-2.9-2.9l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1h-.2a2.1 2.1 0 1 1 0-4.1h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2.1 2.1 0 1 1 2.9-2.9l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5v-.2a2.1 2.1 0 1 1 4.1 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2.1 2.1 0 1 1 2.9 2.9l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1h.2a2.1 2.1 0 1 1 0 4.1h-.1a1.7 1.7 0 0 0-1.5 1Z" />
    </svg>
  );
}

export function AlertIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} width={18} height={18} stroke="var(--warn)" {...props}>
      <path d="M12 3 22 20H2L12 3Z" />
      <path d="M12 10v4.4" />
      <circle cx="12" cy="17.4" r="0.9" fill="var(--warn)" stroke="none" />
    </svg>
  );
}

export function LogoMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" {...props}>
      <path
        d="M4 16 L9 8 L14 13 L20 5"
        stroke="#0a0b12"
        strokeWidth={2.4}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

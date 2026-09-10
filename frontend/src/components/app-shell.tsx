"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import type { ReactNode, ComponentType, SVGProps } from "react";
import {
  TodayIcon,
  OverviewIcon,
  FinanceIcon,
  ProductionIcon,
  ProcurementIcon,
  ProductsIcon,
  AdsIcon,
  StockIcon,
  ControlIcon,
  AgentsIcon,
  ReportsIcon,
  SettingsIcon,
  LogoMark,
} from "@/components/icons";

type NavItem = {
  href: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
};

// Same 12 sections as pages/*.py + app.py's FULL_NAV, same order. Only
// "/" (Сегодня) has a real page during the migration -- the rest link out
// to placeholders until each is rebuilt; see
// memory/project_marketshelper_frontend_migration.md.
const NAV: NavItem[] = [
  { href: "/", label: "Сегодня", icon: TodayIcon },
  { href: "/overview", label: "Обзор", icon: OverviewIcon },
  { href: "/finance", label: "Финансы", icon: FinanceIcon },
  { href: "/production", label: "Производство", icon: ProductionIcon },
  { href: "/procurement", label: "Закупки", icon: ProcurementIcon },
  { href: "/products", label: "Товары", icon: ProductsIcon },
  { href: "/ads", label: "Реклама", icon: AdsIcon },
  { href: "/stock", label: "Остатки", icon: StockIcon },
  { href: "/control", label: "Контроль", icon: ControlIcon },
  { href: "/agents", label: "Агенты", icon: AgentsIcon },
  { href: "/reports", label: "Отчёты", icon: ReportsIcon },
];

// Bottom tab bar keeps the 4 highest-traffic sections plus "Ещё", which opens
// the full 12-item drawer -- a real information-architecture change for
// mobile, not the desktop sidebar squeezed into a smaller viewport. Mirrors
// the mobile artboard in the locked "MARKETSHELPER Redesign" mockup.
const MOBILE_TABS: NavItem[] = [NAV[0], NAV[2], NAV[5], NAV[7]];

function NavLink({ item, active, onClick }: { item: NavItem; active: boolean; onClick?: () => void }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onClick}
      className={
        "flex items-center gap-2.5 rounded-[10px] px-3 py-2.25 text-[13.5px] transition-colors " +
        (active
          ? "bg-accent font-semibold text-accent-foreground"
          : "text-muted-foreground hover:bg-secondary hover:text-foreground")
      }
    >
      <Icon />
      {item.label}
    </Link>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <div className="relative flex min-h-screen w-full">
      {/* Decorative glow blobs live in their own clipped, viewport-sized
          layer -- overflow-hidden here (not on the row below) so the
          negative offsets never add a stray horizontal scrollbar, without
          also turning the aside/main row into a competing scroll container
          (that broke position:sticky on the sidebar -- see git history). */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div
          className="absolute -top-56 -left-44 h-[560px] w-[560px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(124,108,246,.17), transparent 70%)" }}
        />
        <div
          className="absolute -bottom-64 -right-52 h-[600px] w-[600px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(124,108,246,.08), transparent 70%)" }}
        />
      </div>

      {/* Desktop sidebar -- md and up only. Sticky + its own scroll: on a
          long page (charts, tables) the nav shouldn't scroll away with the
          content -- it's the one thing that should always stay reachable. */}
      <aside className="relative z-10 sticky top-0 hidden h-screen w-60 shrink-0 flex-col gap-1 overflow-y-auto border-r border-border bg-card px-3.5 py-5.5 md:flex">
        <div className="flex items-center gap-2.5 px-2.5 pb-5.5">
          <div className="flex size-6.5 items-center justify-center rounded-lg bg-primary">
            <LogoMark />
          </div>
          <span className="text-[14.5px] font-bold tracking-tight">MARKETSHELPER</span>
        </div>

        <nav className="flex flex-col gap-0.5">
          {NAV.map((item) => (
            <NavLink key={item.href} item={item} active={pathname === item.href} />
          ))}
        </nav>

        <div className="flex-1" />

        <NavLink item={{ href: "/settings", label: "Настройки", icon: SettingsIcon }} active={pathname === "/settings"} />

        <div className="mt-2 flex w-fit items-center gap-1.5 rounded-full bg-good-soft px-2.5 py-1.5 text-[11.5px] font-semibold text-good">
          <span className="inline-block size-1.5 rounded-full bg-good" />
          API подключён
        </div>
      </aside>

      {/* Mobile top bar -- below md only */}
      <div className="fixed inset-x-0 top-0 z-20 flex items-center justify-between border-b border-border bg-card px-4.5 py-3 md:hidden">
        <div className="flex items-center gap-2">
          <div className="flex size-6 items-center justify-center rounded-lg bg-primary">
            <LogoMark />
          </div>
          <span className="text-[13.5px] font-bold tracking-tight">MARKETSHELPER</span>
        </div>
        <button
          onClick={() => setDrawerOpen(true)}
          aria-label="Открыть меню разделов"
          className="flex size-9.5 items-center justify-center rounded-[10px] border border-border bg-card text-muted-foreground transition-transform active:scale-90"
        >
          <svg width={17} height={17} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
        </button>
      </div>

      {/* Mobile nav drawer -- full 12-item list, opened from the hamburger.
          Always mounted (not conditionally rendered) so the close path gets
          the same transition as open, instead of an instant unmount --
          spatial consistency: it should visibly retreat to where it came
          from, not vanish. */}
      <div
        className="fixed inset-0 z-30 md:hidden"
        style={{ pointerEvents: drawerOpen ? "auto" : "none" }}
        inert={!drawerOpen}
      >
        <div
          className="absolute inset-0 bg-black/60 transition-opacity"
          style={{ opacity: drawerOpen ? 1 : 0, transitionDuration: "200ms", transitionTimingFunction: "var(--ease-out)" }}
          onClick={() => setDrawerOpen(false)}
        />
        <div
          className="absolute inset-y-0 right-0 flex w-72 flex-col gap-1 border-l border-border bg-card px-3.5 py-5.5 transition-transform"
          style={{
            transform: drawerOpen ? "translateX(0)" : "translateX(100%)",
            transitionDuration: drawerOpen ? "240ms" : "180ms",
            transitionTimingFunction: "var(--ease-drawer)",
          }}
        >
            <div className="flex items-center justify-between px-2.5 pb-4">
              <span className="text-[13.5px] font-bold tracking-tight">Разделы</span>
              <button
                onClick={() => setDrawerOpen(false)}
                aria-label="Закрыть меню"
                className="flex size-8 items-center justify-center rounded-[10px] text-muted-foreground transition-transform active:scale-90"
              >
                ✕
              </button>
            </div>
            {NAV.map((item) => (
              <NavLink key={item.href} item={item} active={pathname === item.href} onClick={() => setDrawerOpen(false)} />
            ))}
            <div className="flex-1" />
            <NavLink
              item={{ href: "/settings", label: "Настройки", icon: SettingsIcon }}
              active={pathname === "/settings"}
              onClick={() => setDrawerOpen(false)}
            />
        </div>
      </div>

      {/* Mobile bottom tab bar -- below md only */}
      <div className="fixed inset-x-0 bottom-0 z-20 flex items-center justify-around border-t border-border bg-card px-1.5 pb-3.5 pt-2.5 md:hidden">
        {MOBILE_TABS.map((item) => {
          const active = pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className="flex min-h-11 min-w-11 flex-col items-center justify-center gap-0.75"
            >
              <Icon color={active ? "var(--accent-foreground)" : "var(--text-faint)"} />
              <span className={"text-[10px] " + (active ? "font-semibold text-accent-foreground" : "text-text-faint")}>
                {item.label}
              </span>
            </Link>
          );
        })}
        <button
          onClick={() => setDrawerOpen(true)}
          className="flex min-h-11 min-w-11 flex-col items-center justify-center gap-0.75"
        >
          <svg width={17} height={17} viewBox="0 0 24 24" fill="none" stroke="var(--text-faint)" strokeLinecap="round">
            <circle cx="5" cy="12" r="1.4" fill="var(--text-faint)" stroke="none" />
            <circle cx="12" cy="12" r="1.4" fill="var(--text-faint)" stroke="none" />
            <circle cx="19" cy="12" r="1.4" fill="var(--text-faint)" stroke="none" />
          </svg>
          <span className="text-[10px] text-text-faint">Ещё</span>
        </button>
      </div>

      <main className="relative z-10 min-w-0 flex-1 pt-14 pb-20 md:pt-0 md:pb-0">{children}</main>
    </div>
  );
}

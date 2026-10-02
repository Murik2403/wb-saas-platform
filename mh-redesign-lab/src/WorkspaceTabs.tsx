import { useEffect, useState, type ReactNode } from "react";
import "./WorkspaceTabs.css";

export type WorkspaceTab = { id: string; label: string };

type Props = {
  tabs: readonly WorkspaceTab[];
  /** Accessible name of the tab bar, e.g. "Разделы товаров". */
  ariaLabel: string;
  /** Panel content by tab id. Only the selected panel is mounted, so hidden panels never fetch or poll. */
  panels: Record<string, ReactNode>;
  /** localStorage key that remembers the last tab (per viewer, best effort). */
  storageKey?: string;
  /** In-app deep-link target (see selectPage(page, target)); `targetMap` says which tab shows it. */
  target?: string | null;
  targetMap?: Record<string, string>;
  /** Tab to open when nothing is remembered; defaults to the first one. */
  initial?: string;
};

function readStored(key: string | undefined, ids: string[]): string | null {
  if (!key) return null;
  try {
    const value = window.localStorage.getItem(key);
    return value && ids.includes(value) ? value : null;
  } catch {
    return null;
  }
}

/** Same look and behaviour as the production page tabs (nav of toggle buttons), reusable on any page. */
export function WorkspaceTabs({ tabs, ariaLabel, panels, storageKey, target, targetMap, initial }: Props) {
  const ids = tabs.map((tab) => tab.id);
  const fromTarget = (value?: string | null) => (value && targetMap && ids.includes(targetMap[value]) ? targetMap[value] : null);
  const [selected, setSelected] = useState<string>(() => fromTarget(target) ?? readStored(storageKey, ids) ?? (initial && ids.includes(initial) ? initial : ids[0]));

  useEffect(() => {
    const next = fromTarget(target);
    if (next) setSelected(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  const choose = (id: string) => {
    setSelected(id);
    if (!storageKey) return;
    try { window.localStorage.setItem(storageKey, id); } catch { /* per-viewer convenience only */ }
  };

  const active = ids.includes(selected) ? selected : ids[0];
  return (
    <div className="workspace-tabs-root">
      <nav className="workspace-tabs" aria-label={ariaLabel}>
        {tabs.map((tab) => (
          <button type="button" key={tab.id} aria-pressed={active === tab.id} aria-controls={`workspace-panel-${tab.id}`} onClick={() => choose(tab.id)}>
            {tab.label}
          </button>
        ))}
      </nav>
      <div className="workspace-tab-panel" id={`workspace-panel-${active}`} role="region" aria-label={tabs.find((tab) => tab.id === active)?.label}>
        {panels[active]}
      </div>
    </div>
  );
}

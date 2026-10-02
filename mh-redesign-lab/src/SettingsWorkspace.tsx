import type { ReactNode } from "react";
import { WorkspaceTabs } from "./WorkspaceTabs";
import "./SettingsWorkspace.css";

const tabs = [
  { id: "connections", label: "Подключения" },
  { id: "data", label: "Данные" },
  { id: "production", label: "Производство" },
] as const;

// in-app deep-link targets (selectPage("settings", target)) -> owning tab
const targetMap = Object.fromEntries(["production-norms", "product-costs", "material-inventory", "manual-expenses", "tax-settings"].map((id) => [id, "production"]));

type Props = { connections: ReactNode; data: ReactNode; production: ReactNode; backups: ReactNode; sectionTarget?: string | null };

/** Backups are one small block, so they live at the end of the «Данные» tab next to sync and import. */
export function SettingsWorkspace({ connections, data, production, backups, sectionTarget }: Props) {
  return <div className="settings-workspace">
    <WorkspaceTabs tabs={tabs} ariaLabel="Разделы настроек" storageKey="mh.tab.settings" target={sectionTarget} targetMap={targetMap}
      keepMounted={tabs.map((tab) => tab.id)}
      panels={{ connections, data: <>{data}{backups}</>, production }} />
  </div>;
}

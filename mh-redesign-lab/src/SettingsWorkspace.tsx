import { useEffect, useState, type ReactNode } from "react";
import "./SettingsWorkspace.css";

const categories = [
  { id: "connections", label: "Подключения" },
  { id: "data", label: "Данные" },
  { id: "production", label: "Производство" },
  { id: "backups", label: "Резервные копии" },
] as const;

export function SettingsWorkspace(props: Record<(typeof categories)[number]["id"], ReactNode> & { sectionTarget?: string | null }) {
  const targetCategory = (target?: string | null): (typeof categories)[number]["id"] | null =>
    ["production-norms", "product-costs", "material-inventory", "manual-expenses"].includes(target ?? "") ? "production" : null;
  const [selected, setSelected] = useState<(typeof categories)[number]["id"]>(() => targetCategory(props.sectionTarget) ?? "connections");
  useEffect(() => {
    const category = targetCategory(props.sectionTarget);
    if (category) setSelected(category);
  }, [props.sectionTarget]);
  return <div className="settings-workspace">
    <nav className="settings-category-navigation" aria-label="Разделы настроек">
      {categories.map((category) => <button className={selected === category.id ? "selected" : ""} type="button" key={category.id}
        aria-pressed={selected === category.id} aria-controls={`settings-${category.id}`}
        onClick={() => setSelected(category.id)}>{category.label}</button>)}
    </nav>
    {categories.map((category) => <div className="settings-category-panel" id={`settings-${category.id}`} key={category.id}
      hidden={selected !== category.id} role="region" aria-label={category.label}>
      {props[category.id]}
    </div>)}
  </div>;
}

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

const key = "marketshelper-theme";
function savedTheme(): "light" | "dark" {
  try { return localStorage.getItem(key) === "light" ? "light" : "dark"; }
  catch { return document.documentElement.dataset.theme === "light" ? "light" : "dark"; }
}
function applyTheme(theme: "light" | "dark") {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#f3f6f8" : "#07090d");
}

export function ThemeToggle() {
  const [theme, setTheme] = useState(savedTheme);
  useEffect(() => {
    applyTheme(theme);
    const sync = (event: StorageEvent) => {
      if (event.key === key || event.key === null) setTheme(savedTheme());
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [theme]);
  const label = theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему";
  return <button type="button" className="secondary-button theme-toggle" aria-label={label} title={label} aria-pressed={theme === "light"} onClick={() => {
    const next = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
    try { localStorage.setItem(key, next); } catch { /* Preference still applies for this visit. */ }
  }}>{theme === "dark" ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}<span>{theme === "dark" ? "Светлая тема" : "Тёмная тема"}</span></button>;
}

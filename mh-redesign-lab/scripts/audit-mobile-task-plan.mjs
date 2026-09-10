import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const failures = [];

if (!app.includes('aria-label="Закрыть план задач"')) {
  failures.push("task plan has no accessible close control");
}
if (!app.includes("В работу")) {
  failures.push("blocked task has no return-to-work action");
}
if (!app.includes("Готово")) {
  failures.push("in-progress task has no completion action");
}
if (!styles.includes(".plan-drawer:not(.operation-dialog) {\n    top: var(--mobile-status-bar);")) {
  failures.push("task plan can render under the mobile status bar");
}
if (!styles.includes(".drawer-close-button")) {
  failures.push("task plan close control has no dedicated visible style");
}

console.log(JSON.stringify({ checked: "mobile-task-plan", failures }, null, 2));
if (failures.length) process.exitCode = 1;

import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./CabinetUx.css";
import "./Theme.css";
import { trackMetrikaGoal } from "./metrika";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

const goal = new URLSearchParams(window.location.search).get("mh_goal");
if (goal) {
  void trackMetrikaGoal(goal).then(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete("mh_goal");
    window.history.replaceState(null, "", url);
  });
}

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js");
  });
}

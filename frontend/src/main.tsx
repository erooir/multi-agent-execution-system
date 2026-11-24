import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./light-workspace.css";
import "./wb-shell.css";

// A browser reload opens the project workdesk. Fresh links still open their target.
const [navigation] = performance.getEntriesByType("navigation") as PerformanceNavigationTiming[];
if (navigation?.type === "reload" && window.location.pathname.replace(/\/$/, "") === "/manage") {
  const url = new URL(window.location.href);
  for (const key of ["view", "tab", "asset", "task", "task_case", "connector"]) {
    url.searchParams.delete(key);
  }
  if (url.href !== window.location.href) {
    window.history.replaceState(window.history.state, "", url);
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

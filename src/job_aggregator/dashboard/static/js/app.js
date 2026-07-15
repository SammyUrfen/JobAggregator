// Minimal vanilla JS (Phase 8). No framework, no CDN. Three jobs:
//   1) theme toggle -> stamp data-theme on <html>, persist to localStorage
//   2) "Run now" -> POST /api/runs, then poll GET /api/runs/current until done
//   3) row actions -> POST /api/jobs/{uid}/action {field, value}
// Filters are plain GET query params (server-rendered), so no JS needed for those.

(function () {
  "use strict";

  // ---- 1) theme toggle -----------------------------------------------------
  const THEME_KEY = "jobagg-theme";
  function applyTheme(t) {
    if (t === "light" || t === "dark") {
      document.documentElement.setAttribute("data-theme", t);
    } else {
      document.documentElement.removeAttribute("data-theme"); // fall back to system
    }
  }
  applyTheme(localStorage.getItem(THEME_KEY));
  window.toggleTheme = function () {
    // TODO(Phase 8): cycle system -> light -> dark and persist
    throw new Error("Phase 8: implement theme toggle");
  };

  // ---- 2) run now + poll ---------------------------------------------------
  window.runNow = async function () {
    // TODO(Phase 8): POST /api/runs (handle 409 already-running), then poll
    // GET /api/runs/current every ~2s, updating the status widget, until finished.
    throw new Error("Phase 8: implement run-now + polling");
  };

  // ---- 3) row actions ------------------------------------------------------
  window.jobAction = async function (uid, field, value) {
    // TODO(Phase 8): POST /api/jobs/${uid}/action with {field, value}; update the row.
    throw new Error("Phase 8: implement row actions");
  };
})();

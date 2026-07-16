// Minimal vanilla JS (Phase 8). No framework, no CDN.
//   1) theme toggle -> stamp data-theme on <html>, persist to localStorage
//   2) "Run now" -> POST /api/runs, then poll GET /api/runs/current until done
//   3) row actions -> POST /api/jobs/{uid}/action, swap the <tr>
//   4) config submit -> PUT /api/config (FormData), surface inline field errors
// Filters are plain GET query params (server-rendered), so no JS needed for those.

(function () {
  "use strict";

  const THEME_KEY = "jobagg-theme";
  const POLL_INTERVAL_MS = 2000;

  // ---- 1) theme toggle -----------------------------------------------------
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme"); // "light" | "dark" | null
  }
  window.toggleTheme = function () {
    // system -> light -> dark -> system
    const order = { null: "light", light: "dark", dark: null };
    const next = order[String(currentTheme())];
    if (next) {
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem(THEME_KEY, next);
    } else {
      document.documentElement.removeAttribute("data-theme");
      localStorage.removeItem(THEME_KEY);
    }
  };

  // ---- 2) run now + poll ---------------------------------------------------
  let polling = null;

  function setPill(status) {
    const pill = document.getElementById("rs-pill");
    if (pill) {
      pill.textContent = status;
      pill.className = "pill pill-" + status;
    }
  }
  function setCount(id, value) {
    const el = document.getElementById(id);
    if (el && value !== undefined && value !== null) el.textContent = value;
  }

  async function poll() {
    try {
      const res = await fetch("/api/runs/current", { headers: { Accept: "application/json" } });
      const data = await res.json();
      if (data.status && data.status !== "idle") setPill(data.status);
      if (data.counts) {
        setCount("rs-new", data.counts.new);
        setCount("rs-updated", data.counts.updated);
        setCount("rs-expired", data.counts.expired);
      }
      if (data.status !== "running") {
        clearInterval(polling);
        polling = null;
        const btn = document.getElementById("run-now-btn");
        if (btn) btn.disabled = false;
      }
    } catch (e) {
      clearInterval(polling);
      polling = null;
    }
  }

  window.runNow = async function () {
    const btn = document.getElementById("run-now-btn");
    try {
      const res = await fetch("/api/runs", { method: "POST", headers: { Accept: "application/json" } });
      if (res.status === 409) {
        alert("A run is already in progress.");
        return;
      }
      if (!res.ok) {
        alert("Could not start a run.");
        return;
      }
      if (btn) btn.disabled = true;
      setPill("running");
      if (!polling) polling = setInterval(poll, POLL_INTERVAL_MS);
    } catch (e) {
      alert("Could not start a run.");
    }
  };

  // ---- 3) delegated row actions -------------------------------------------
  document.addEventListener("click", async function (ev) {
    const btn = ev.target.closest ? ev.target.closest(".row-action") : null;
    if (!btn) return;
    const uid = btn.getAttribute("data-uid");
    const action = btn.getAttribute("data-action");
    try {
      const res = await fetch("/api/jobs/" + encodeURIComponent(uid) + "/action", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/html" },
        body: JSON.stringify({ action: action }),
      });
      if (!res.ok) {
        let msg = "Action failed.";
        try { msg = (await res.json()).error.message; } catch (_) {}
        alert(msg);
        return;
      }
      const html = await res.text();
      const row = btn.closest("tr");
      if (row) row.outerHTML = html;
    } catch (e) {
      alert("Action failed.");
    }
  });

  // ---- 4) config submit ----------------------------------------------------
  const configForm = document.getElementById("config-form");
  if (configForm) {
    configForm.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      document.querySelectorAll(".field-error").forEach((el) => (el.textContent = ""));
      const banner = document.getElementById("config-banner");
      try {
        const res = await fetch("/api/config", { method: "PUT", body: new FormData(configForm) });
        const data = await res.json();
        if (res.ok) {
          if (banner) { banner.textContent = data.message || "Saved."; banner.className = "banner ok"; }
        } else {
          const errors = (data.error && data.error.details && data.error.details.errors) || [];
          errors.forEach(function (e) {
            const slot = document.querySelector('[data-field-error="' + e.field + '"]');
            if (slot) slot.textContent = e.message;
          });
          if (banner) { banner.textContent = "Please fix the errors above."; banner.className = "banner err"; }
        }
      } catch (e) {
        if (banner) { banner.textContent = "Save failed."; banner.className = "banner err"; }
      }
    });
  }
})();

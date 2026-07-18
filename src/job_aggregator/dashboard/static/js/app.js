// Minimal vanilla JS (Phase 8, +Track B cards/modal). No framework, no CDN.
//   1) theme toggle -> stamp data-theme on <html>, persist to localStorage
//   2) "Run now" -> POST /api/runs, then poll GET /api/runs/current until done
//   3) job actions (apply/bookmark/hide) -> POST /api/jobs/{uid}/action, swap the .job-card
//   4) config submit -> PUT /api/config (FormData), surface inline field errors
//   5) card click -> open detail modal (GET /api/jobs/{uid}/detail); Apply -> open posting + apply
// Filters are plain GET query params (server-rendered), so no JS needed for those.

(function () {
  "use strict";

  const THEME_KEY = "jobagg-theme";
  const POLL_INTERVAL_MS = 2000;

  // uid -> a safe [data-uid="…"] attribute selector (CSS.escape where available).
  function uidSel(uid) {
    const esc = window.CSS && CSS.escape ? CSS.escape(uid) : uid.replace(/["\\]/g, "\\$&");
    return '[data-uid="' + esc + '"]';
  }

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

  function stopPolling(btn) {
    if (polling) { clearInterval(polling); polling = null; }
    if (btn) btn.disabled = false;
  }

  window.runNow = async function () {
    const btn = document.getElementById("run-now-btn");
    // Immediate feedback: the trigger blocks for the whole cycle, so show 'running' and start the
    // poller NOW (the run row is created at cycle start) rather than after the POST returns.
    if (btn) btn.disabled = true;
    setPill("running");
    if (!polling) polling = setInterval(poll, POLL_INTERVAL_MS);
    try {
      const res = await fetch("/api/runs", { method: "POST", headers: { Accept: "application/json" } });
      if (res.status === 409) {
        alert("A run is already in progress.");
        return; // keep polling — the in-progress run will report completion
      }
      if (!res.ok) {
        let msg = "Could not start a run.";
        try { msg = (await res.json()).error.message; } catch (_) {}
        alert(msg);
        stopPolling(btn);
      }
    } catch (e) {
      alert("Could not start a run.");
      stopPolling(btn);
    }
  };

  // ---- 3) job actions: apply / bookmark / hide ----------------------------
  // Post an action, swap the affected card, and (if the modal shows this job) refresh it so both
  // views stay consistent. Returns true on success.
  async function postAction(uid, action) {
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
        return false;
      }
      const html = await res.text();
      const card = document.querySelector(".job-card" + uidSel(uid));
      if (card) card.outerHTML = html;
      const modal = document.getElementById("job-modal");
      if (modal && !modal.hidden && modal.querySelector(".jd" + uidSel(uid))) {
        await refreshModal(uid); // reflect the new applied/bookmarked/hidden state in the open modal
      }
      return true;
    } catch (e) {
      alert("Action failed.");
      return false;
    }
  }

  // ---- 6) tailor résumé (Track D Step 0) ----------------------------------
  // POST /api/jobs/{uid}/tailor and inject the returned preview partial into the modal footer.
  async function postTailor(uid, btn) {
    const container = document.querySelector(".jd-tailor" + uidSel(uid));
    if (btn) { btn.disabled = true; btn.textContent = "Tailoring…"; }
    if (container) container.innerHTML = '<p class="muted">Tailoring…</p>';
    try {
      const res = await fetch("/api/jobs/" + encodeURIComponent(uid) + "/tailor", {
        method: "POST",
        headers: { Accept: "text/html" },
      });
      if (!res.ok) {
        let msg = "Tailoring failed.";
        try { msg = (await res.json()).error.message; } catch (_) {}
        alert(msg);
        if (container) container.innerHTML = "";
        return;
      }
      if (container) container.innerHTML = await res.text();
    } catch (e) {
      alert("Tailoring failed.");
      if (container) container.innerHTML = "";
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Tailor résumé"; }
    }
  }

  // ---- 8) auto-apply: launch the local headful fill agent (when apply.enabled) -----------
  // NOTE: launching does NOT mark the job applied — the agent CLI sets the flag itself once
  // the form fill actually completes (a launch that dies must not leave a phantom ✓).
  async function postApply(uid) {
    try {
      const res = await fetch("/api/jobs/" + encodeURIComponent(uid) + "/apply", {
        method: "POST", headers: { Accept: "application/json" },
      });
      const data = await res.json();
      alert(data.message || (res.ok ? "Launched." : "Could not launch."));
      if (res.ok && data.ok) pollApplyStatus(); // reveal the Stop button immediately
    } catch (e) { alert("Could not launch the apply agent."); }
  }

  // ---- 9) apply kill switch: poll for live agents, show/hide the header Stop button --------
  const applyStopBtn = () => document.getElementById("apply-stop-btn");

  async function pollApplyStatus() {
    const btn = applyStopBtn();
    if (!btn) return;
    try {
      const res = await fetch("/api/apply/status", { headers: { Accept: "application/json" } });
      const data = await res.json();
      btn.hidden = !(data.running > 0);
    } catch (e) { /* leave the button as-is on a transient error */ }
  }

  window.stopApply = async function () {
    const btn = applyStopBtn();
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/apply/stop", { method: "POST", headers: { Accept: "application/json" } });
      const data = await res.json();
      alert(data.message || "Stop requested.");
    } catch (e) {
      alert("Could not reach the stop endpoint.");
    } finally {
      if (btn) btn.disabled = false;
      pollApplyStatus();
    }
  };

  // Poll every few seconds so the Stop button appears/disappears on its own — the kill switch
  // must be reachable even after a page reload or with the modal closed.
  setInterval(pollApplyStatus, 4000);
  pollApplyStatus();

  // ---- 5) detail modal -----------------------------------------------------
  let lastOpener = null; // restore focus here when the modal closes (a11y)

  async function refreshModal(uid) {
    const body = document.getElementById("job-modal-body");
    const res = await fetch("/api/jobs/" + encodeURIComponent(uid) + "/detail", {
      headers: { Accept: "text/html" },
    });
    if (res.ok && body) body.innerHTML = await res.text();
    return res.ok;
  }

  async function openModal(uid, opener) {
    const modal = document.getElementById("job-modal");
    if (!modal) return;
    const ok = await refreshModal(uid);
    if (!ok) { alert("Could not load job details."); return; }
    lastOpener = opener || null;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    const close = modal.querySelector("[data-modal-close].modal-close");
    if (close) close.focus();
  }

  function closeModal() {
    const modal = document.getElementById("job-modal");
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    document.getElementById("job-modal-body").innerHTML = "";
    document.body.classList.remove("modal-open");
    if (lastOpener && lastOpener.focus) lastOpener.focus();
    lastOpener = null;
  }

  // One delegated click handler, most-specific target first.
  document.addEventListener("click", function (ev) {
    if (!ev.target.closest) return;

    // close the modal (✕ button or backdrop)
    if (ev.target.closest("[data-modal-close]")) { closeModal(); return; }

    // Apply: when apply.enabled -> launch the local fill agent; else open the posting + mark applied
    const applyBtn = ev.target.closest("[data-apply-uid]");
    if (applyBtn) {
      const uid = applyBtn.getAttribute("data-apply-uid");
      if (applyBtn.getAttribute("data-apply-mode") === "agent") {
        postApply(uid);
      } else {
        const url = applyBtn.getAttribute("data-apply-url");
        if (url) window.open(url, "_blank", "noopener");
        postAction(uid, "apply");
      }
      return;
    }

    // Tailor résumé: generate a role-tailored PDF from the profile, inject the preview
    const tailorBtn = ev.target.closest("[data-tailor-uid]");
    if (tailorBtn) {
      postTailor(tailorBtn.getAttribute("data-tailor-uid"), tailorBtn);
      return;
    }

    // apply/bookmark/hide toggle (in a card or the modal footer)
    const actionBtn = ev.target.closest(".row-action");
    if (actionBtn) {
      postAction(actionBtn.getAttribute("data-uid"), actionBtn.getAttribute("data-action"));
      return;
    }

    // click anywhere else on a card (not a button/link) -> open its detail modal
    const card = ev.target.closest(".job-card");
    if (card && !ev.target.closest("button, a")) {
      openModal(card.getAttribute("data-uid"), card);
    }
  });

  // keyboard: Esc closes the modal; Enter/Space opens a focused card.
  document.addEventListener("keydown", function (ev) {
    const modal = document.getElementById("job-modal");
    if (ev.key === "Escape" && modal && !modal.hidden) { closeModal(); return; }
    const active = document.activeElement;
    if ((ev.key === "Enter" || ev.key === " ") && active && active.classList.contains("job-card")) {
      ev.preventDefault();
      openModal(active.getAttribute("data-uid"), active);
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
        // Browsers OMIT unchecked checkboxes from FormData, which would make every toggle
        // one-way (on-only). Force an explicit true/false for each so a box can be turned OFF.
        const fd = new FormData(configForm);
        configForm.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
          fd.set(cb.name, cb.checked ? "true" : "false");
        });
        const res = await fetch("/api/config", { method: "PUT", body: fd });
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

  // ---- 7) profile editor: PUT the YAML, surface validation errors in the banner ----------
  const profileForm = document.getElementById("profile-form");
  if (profileForm) {
    profileForm.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const banner = document.getElementById("profile-banner");
      try {
        const res = await fetch("/api/profile", { method: "PUT", body: new FormData(profileForm) });
        const data = await res.json();
        if (res.ok) {
          if (banner) { banner.textContent = data.message || "Saved."; banner.className = "banner ok"; }
        } else {
          const msg = (data.error && data.error.message) || "Invalid profile.";
          if (banner) { banner.textContent = msg; banner.className = "banner err"; }
        }
      } catch (e) {
        if (banner) { banner.textContent = "Save failed."; banner.className = "banner err"; }
      }
    });
  }
})();

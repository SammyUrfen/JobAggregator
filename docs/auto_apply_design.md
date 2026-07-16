# Auto-Apply extension — design & feasibility (research-grounded)

> Status: **building.** Grounded in a verified deep-research pass (2026-07-16, 25 sources,
> 20 confirmed / 5 refuted claims). This is the "generate → agent-fills → human-reviews →
> human-submits" architecture, plus the reboot-durable service and card UI.
>
> **Decisions (locked 2026-07-16):** submit model = **agent fills → you review → you submit**;
> platforms = **both** (ATS as the reliable core; LinkedIn/Naukri best-effort with anti-bot
> caveats); LLM backend = **both a configurable OpenAI-compatible endpoint AND a coding-agent
> driver** (you'll use Claude Code) behind one adapter interface; build order = **A → B → C → D**.
>
> **Track A (durable service + notify + port): DONE.** `docker-compose.yml` (restart-on-boot,
> health-checked, named volume, non-8000 port `JOBAGG_PORT` default 8770); Telegram end-of-run
> summary (`notify_run` → `build_run_summary`) with a dashboard link driven by `JOBAGG_PUBLIC_URL`
> / `notify.dashboard_url`; startup catch-up (≥24h-since-success gate) already covered reboots.
> 250 tests green, 89% cov.

## Problem

Turn the aggregator (which finds + dedups + filters jobs) into a tool that also **helps apply**:
click a job → see full details in a modal → generate a role-tailored résumé from your real
profile → have an agent pre-fill the application form → you review and submit. Plus: run
reboot-durably in the background, on a non-8000 port, and Telegram-notify a summary + link when a
cycle finishes.

## Goals

- **G1** Card-based job list; click a card → detail modal with the full posting, a link to the
  original, and an **Apply** action.
- **G2** Per-role résumé tailoring from a **provided LaTeX template** + a structured **profile**
  (projects, skills, ambitions) — **truthful**, ATS-parseable.
- **G3** An agent that reads the application form and **pre-fills** it; **you** handle any auth
  (once, persisted) and **you** click submit.
- **G4** Configurable LLM backend (OpenAI-compatible endpoint / local) — reuse the existing
  swap-friendly client pattern.
- **G5** Reboot-durable background service (Docker), configurable port, Telegram run-summary +
  localhost link on completion; startup catch-up already gated on "no success in ~24h".

## Non-goals (deliberate, research-driven)

- **NG1 — No blind unattended auto-submit.** The durable legal exposure is *breach of contract*:
  once an agent logs in and click-accepts a ToS that bans automation, those terms are enforceable
  (hiQ v. LinkedIn — won on CFAA for *public* scraping, **lost $500K on contract**). Keeping the
  human as the one who accepts ToS and clicks **Submit** is the safe, durable pattern — and it's
  also what actually works, because self-hosted browser agents can't beat anti-bot/CAPTCHA.
- **NG2 — No résumé fabrication.** Tailoring = re-ordering/emphasizing **real** facts from your
  profile, never inventing employers, dates, or skills. Enforced structurally (below), not just by
  prompt.
- **NG3 — No mass LinkedIn/Naukri automation.** Those platforms actively detect+ban automation and
  gate behind CAPTCHA (we watched Naukri return `406 recaptcha` to jobspy). ATS forms first.

## Research-grounded decisions

| Decision | Choice | Why (cited) |
|---|---|---|
| Browser driver | **browser-use** (self-hosted) | MIT, Playwright-based (DOM-first), backend-agnostic: OpenAI-compatible `base_url`, any LiteLLM model, **local Ollama** — runs with *no* cloud LLM. Built-in Playwright-format session persistence. [docs.browser-use.com] |
| Not Skyvern/Stagehand (yet) | — | Skyvern's anti-bot/proxy/CAPTCHA are **cloud-only** (self-hosted just pauses 30s for manual CAPTCHA); Stagehand leans Browserbase cloud. Fine as future options. |
| Auth persistence | **Playwright `storageState`** (cookies + localStorage), **encrypted at rest** | Reused across runs so you log in once; `{indexedDB:true}` opt-in since v1.51; **sessionStorage is NOT persisted** and **server-side token expiry still forces periodic re-login**. Encrypt with Fernet/libsodium (keychain-derived key). |
| Submit model | **Agent fills → you review → you submit** | NG1. Also the only reliable pattern given anti-bot walls; browser-use itself notes "fully autonomous multi-step tasks still need human-in-the-loop checkpoints", and a 25-field form is where autonomous filling gets flaky. |
| Résumé pipeline | **ResumeFlow-style 3-step** (extract JD → extract profile facts → generate) **+ layered anti-fabrication** | Off-the-shelf, no fine-tuning (arXiv 2402.06221). Truthfulness needs deterministic layers, not just a prompt: **merge-exclusion** (LLM text may not enter real employer/experience sections), **content-preservation-vs-alignment scoring** (flag low-preservation/high-alignment = likely hallucination), prompt grounding as *one* layer. |
| Avoid | **Genetic/DE "fitness optimizer" résumé loops** (Synapse) | Anti-pattern: they optimize toward the tool's own match score with **no truthfulness constraint** — they game the scorer and *remove* facts. |
| LLM backend | **Two adapters behind one interface:** a configurable OpenAI-compatible endpoint *and* a local coding-agent driver (Claude Code / Codex / MCP) | User wants both; will personally use Claude Code. One `AgentBackend` protocol (`generate_resume`, `fill_form`) with two impls, chosen in `/config`. Reuses the project's swap-friendly client pattern. |
| Platforms | **Both** — ATS forms are the **reliable core**, LinkedIn Easy Apply / Naukri are **best-effort** | User chose both. ATS (Greenhouse/Lever/Ashby/SmartRecruiters) are mostly public/no-login, simplest, lowest ToS risk. LinkedIn/Naukri stay opt-in, headful-only, with explicit anti-bot/ToS caveats and no auto-submit — the agent *assists*, you accept ToS + submit. |

## Architecture (new components)

```
sources/ (unchanged: fetch → dedup → filter → upsert)
        │
        ▼
profile/           # your ground-truth: projects, skills, education, links, ambitions (YAML/DB)
resume/            # LaTeX template + tailoring pipeline (JD-extract → fact-extract → render → PDF)
  ├── tailor.py    #   3-step LLM pipeline + merge-exclusion + preservation/alignment scoring
  └── render.py    #   fill template → pdflatex/tectonic → PDF (+ store artifact)
apply/             # the agent layer (opt-in, off by default)
  ├── session.py   #   encrypted Playwright storageState per domain; headful login flow
  ├── agent.py     #   browser-use driver: read form → map fields from profile+resume → PRE-FILL
  └── ats/*        #   per-ATS field maps where deterministic (Greenhouse/Lever) to avoid LLM drift
dashboard/         # cards + detail modal + "Apply" (opens headful browser, agent fills, you submit)
notify/telegram    # already exists; add a run-summary digest with the localhost link
```

**Apply flow (per job, human-triggered):** click **Apply** on a card → backend picks/generates the
tailored résumé → launches a **headful** browser-use session (you can watch) → loads saved auth for
that domain (or prompts you to log in once, then persists it encrypted) → the agent locates and
**fills** the form fields from your profile + résumé → **stops at Submit** → you review the filled
form in the real browser and click **Submit** yourself. Nothing is sent without you.

## Reboot-durability + notify (Track A — decision-independent)

- **Dockerfile** already exists (single-process `serve`, auto-init). Add `restart: unless-stopped`
  via a `docker-compose.yml`, a configurable published port, and a named volume for `/data`.
- **Startup catch-up** already fires only when there's been no *success* in ~24h — so a reboot mid-day
  won't double-fetch, and a reboot after a long gap will catch up. No change needed there.
- **Telegram**: the runner's step-8 already builds notifiers; add a **run-summary** message
  (`new/updated/expired`, per-source ok/fail, + `http://<host>:<port>/`) posted when a cycle finishes,
  behind `notify.telegram.enabled` + `TELEGRAM_*` env. (Today Telegram only sends the new-jobs digest.)

## Phased build

- **Phase A — durable service + notify + port. ✅ DONE.** `docker-compose.yml`, `JOBAGG_PORT`,
  Telegram run-summary + dashboard link.
- **Phase B — card UI + detail modal.** Replace the table with cards; modal shows full description,
  original link, salary/meta, and an Apply button. (Pure dashboard work.)
- **Phase C — profile + résumé tailoring.** Profile store; LaTeX template intake; the 3-step pipeline
  with merge-exclusion + preservation scoring; render to PDF; preview in the modal. *Highest-value,
  lowest-risk half of "auto-apply" — useful even without the browser agent.* Introduces the
  `AgentBackend` protocol (OpenAI-compatible **and** coding-agent impls).
- **Phase D — apply agent (opt-in).** browser-use integration; encrypted per-domain session store;
  headful fill-then-review flow. **ATS field maps first (reliable core); LinkedIn/Naukri best-effort,
  headful-only, no auto-submit.** Off by default.

## Risks & how we design around them

- **Anti-bot / CAPTCHA** — real and unbeatable self-hosted (Naukri already blocks jobspy). → Headful,
  human-present, ATS-first, no LinkedIn/Naukri auto-submit. The agent *assists*; it doesn't evade.
- **ToS / breach-of-contract** — the human accepts ToS and submits; the agent never click-accepts a ToS
  on your behalf. ATS public forms minimize logged-in-ToS surface.
- **Résumé fabrication** — structural merge-exclusion + preservation/alignment scores surfaced to you;
  you approve the PDF before it's attached. Never auto-generate claims not in your profile.
- **Auth secrets at rest** — encrypt the storageState blob (Fernet, key from OS keychain / a
  passphrase); never commit it; document that server-side expiry still needs occasional re-login.
- **Form-fill reliability** — deterministic field maps for known ATS; LLM only for the unknown; always
  human-review before submit.

## Still needed from you (for Phase C)

- The **LaTeX résumé template** (the merge-exclusion pipeline is built around its section structure).
- A first draft of the **profile**: projects, skills, education, links, ambitions. I'll scaffold a
  `profile.yaml` you can fill in — this is the ground-truth the tailoring may re-order/emphasize but
  never invent beyond.
- For the OpenAI-compatible backend: which endpoint + model (Groq / Gemini / local). The coding-agent
  backend (Claude Code) needs no key.

## Open research questions (unresolved by the deep-research pass — design around them)

- Per-platform empirical block/ban reality (LinkedIn Easy Apply / Naukri / each ATS) — general law +
  frameworks are covered, platform-level ban rates are not. → headful, human-present, no auto-submit.
- Concrete encryption-at-rest scheme for the persisted session (Fernet vs OS keychain vs SQLCipher).
- Local-model form-fill reliability floor (documented action-schema issues on some Qwen models).
- Whether the tailored LaTeX actually passes real ATS parsers end-to-end.

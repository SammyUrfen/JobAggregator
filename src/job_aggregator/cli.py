"""Command-line entry point.

Subcommands (see PLAN.md Part II for full behaviour):
    initdb        create data/jobs.db and seed config from config/default_config.yaml
    run           execute ONE aggregation cycle now and print a summary   [Phase 5/6]
    serve         launch the FastAPI dashboard (which owns the daily scheduler)  [Phase 8]
    show-config   print the effective config currently stored in the DB
    tailor        tailor the résumé to one job by uid -> a PDF (Track D Step 0)
    apply         fill a job application in a headful browser to review + submit (Track D; local)

Design note: heavy third-party imports (fastapi, jobspy, apscheduler, pydantic) are done
LAZILY inside each handler so that `python -m job_aggregator --help` works with only the
stdlib present — i.e. before `pip install -e .[dev]`. Do not add top-level heavy imports.
"""

from __future__ import annotations

import argparse
import sys

from job_aggregator import __version__
from job_aggregator.errors import JobAggregatorError


def cmd_initdb(args: argparse.Namespace) -> int:
    """Create the DB (schema + seed config row from default_config.yaml)."""
    from pathlib import Path

    from job_aggregator.config.store import seed_from_yaml
    from job_aggregator.logging_setup import configure_logging
    from job_aggregator.storage.db import connect, init_db

    configure_logging(args.log_level)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(args.db)
    init_db(conn)
    seed_from_yaml(conn)
    print(f"initialized database at {args.db}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run one aggregation cycle now."""
    from job_aggregator.clock import SystemClock
    from job_aggregator.config.store import load_effective_config, seed_from_yaml
    from job_aggregator.logging_setup import configure_logging
    from job_aggregator.pipeline.runner import run_cycle
    from job_aggregator.storage import runs_repo
    from job_aggregator.storage.db import connect, init_db

    configure_logging(args.log_level)
    clock = SystemClock()
    conn = connect(args.db)
    init_db(conn)  # idempotent — `run` works even without a prior `initdb`
    seed_from_yaml(conn)  # idempotent — seeds config only if absent
    runs_repo.reconcile_orphan_runs(conn, clock)  # self-heal a run a crash left 'running'
    cfg = load_effective_config(conn)
    summary = run_cycle(conn, cfg, clock, trigger="manual")
    print(summary)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the dashboard (which owns the daily scheduler). Implemented in Phase 8."""
    import os

    import uvicorn

    from job_aggregator.logging_setup import configure_logging

    configure_logging(args.log_level)
    # The uvicorn factory calls create_app() with no kwargs, so --db is passed through the
    # environment (create_app reads JOBAGG_DB). Without this, `serve --db X` would silently serve
    # the default DB — which also broke the "verify against a throwaway --db" safety workflow.
    os.environ["JOBAGG_DB"] = str(args.db)
    # Single process ONLY — never `--workers N`: each worker would spin up its own scheduler,
    # firing the daily cycle N times. The dashboard's lifespan owns exactly one JobScheduler.
    uvicorn.run(
        "job_aggregator.dashboard.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def cmd_show_config(args: argparse.Namespace) -> int:
    """Print the effective config. Implemented in Phase 1."""
    from job_aggregator.config.store import load_effective_config
    from job_aggregator.storage.db import connect

    conn = connect(args.db)
    cfg = load_effective_config(conn)
    print(cfg.model_dump_json(indent=2))
    return 0


def cmd_tailor(args: argparse.Namespace) -> int:
    """Tailor the résumé to one job (by uid) and write a PDF (Track D Step 0; no browser)."""
    from pathlib import Path

    from job_aggregator.config.store import load_effective_config
    from job_aggregator.errors import NotFoundError, RenderError
    from job_aggregator.logging_setup import configure_logging
    from job_aggregator.paths import resumes_dir
    from job_aggregator.profile.store import load_profile
    from job_aggregator.resume.render import compile_pdf, render_latex
    from job_aggregator.resume.tailor import tailor_resume
    from job_aggregator.storage.db import connect

    configure_logging(args.log_level)
    conn = connect(args.db)
    row = conn.execute(
        "SELECT title, description FROM jobs WHERE job_uid = ?", (args.uid,)
    ).fetchone()
    if row is None:
        raise NotFoundError("job not found", details={"uid": args.uid})
    cfg = load_effective_config(conn)  # ConfigError (friendly) if the DB isn't initialized
    profile = load_profile()  # ConfigError (friendly) if profile.yaml is missing
    # Title + description so even a null description still yields keywords from the title.
    jd = f"{row['title']}\n{row['description'] or ''}"
    # Rewrite bullets with the LLM when the config asks (default: Claude Code) or --llm forces it;
    # a missing CLI/key degrades to deterministic selection (try_build_backend returns None).
    backend = None
    if cfg.resume.tailor_with_llm or args.llm:
        from job_aggregator.apply.backends import try_build_backend

        backend = try_build_backend(cfg.resume)
    tailored = tailor_resume(profile, jd, backend=backend, config=cfg.resume)
    print(f"projects: {', '.join(p.name for p in tailored.projects)}")
    print(
        f"preservation: {tailored.preservation:.0%}   keywords matched: {len(tailored.jd_keywords)}"
    )
    for flag in tailored.flags:
        print(f"  ! {flag}")
    out = Path(args.out) if args.out else resumes_dir() / f"{args.uid}.pdf"
    tex = render_latex(profile, tailored)
    try:
        compile_pdf(tex, out)
        print(f"wrote {out}")
    except RenderError as exc:  # no engine / build failed -> keep the text preview + the .tex
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".tex").write_text(tex, encoding="utf-8")
        print(f"PDF not built ({exc.message}); wrote {out.with_suffix('.tex')} instead")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """Fill a job application in a HEADFUL browser for you to review + submit (Track D; run LOCALLY,
    not in headless docker). Needs `pip install -e '.[apply]' && playwright install chromium`."""
    import shutil

    from job_aggregator.apply.agent import apply_to_job
    from job_aggregator.apply.driver import BrowserDriver, PlaywrightDriver
    from job_aggregator.config.store import load_effective_config
    from job_aggregator.errors import NotFoundError
    from job_aggregator.logging_setup import configure_logging
    from job_aggregator.models.job import Job
    from job_aggregator.paths import data_dir
    from job_aggregator.profile.store import load_profile
    from job_aggregator.storage.db import connect

    configure_logging(args.log_level)
    conn = connect(args.db)
    row = conn.execute("SELECT * FROM jobs WHERE job_uid = ?", (args.uid,)).fetchone()
    if row is None:
        raise NotFoundError("job not found", details={"uid": args.uid})
    cfg = load_effective_config(conn)
    profile = load_profile()
    # One LLM backend, used for BOTH résumé tailoring (folds into the tailor_resume call below)
    # and, on the deterministic driver, Set-of-Marks grounding. Config-driven (Claude Code by
    # default); a missing CLI/key -> None -> deterministic selection + generic fills. --llm forces
    # it on even if resume.tailor_with_llm is off.
    from job_aggregator.apply.backends import try_build_backend

    llm_backend = (
        try_build_backend(cfg.resume) if (cfg.resume.tailor_with_llm or args.llm) else None
    )

    # Engine choice: "agentic" = a Claude session drives the visible browser via the playwright
    # MCP (reaches the form from a posting page, waits with you through captcha/login walls).
    # Falls back to the deterministic selector fill when the claude/npx CLIs are missing —
    # an absent tool must degrade the experience, never kill the apply.
    driver: BrowserDriver
    claude_bin = (cfg.resume.agent_command or ["claude"])[0]
    if cfg.apply.engine == "agentic" and shutil.which(claude_bin) and shutil.which("npx"):
        from job_aggregator.apply.agentic import AgenticSession

        driver = AgenticSession(
            claude_bin=claude_bin,
            timeout_s=float(cfg.apply.agent_timeout_s),
            model=cfg.apply.agent_model or None,
            use_browser_cookies=cfg.apply.use_browser_cookies,
            cookie_db=cfg.apply.browser_cookie_db or None,
            log_path=str(data_dir() / "apply_agent.log"),
        )
        print(
            "engine: agentic — Claude drives the browser window; if a captcha or login "
            "appears, solve it there and the agent continues."
        )
    else:
        if cfg.apply.engine == "agentic":
            print(
                f"agentic engine unavailable ({claude_bin!r} or npx not on PATH); "
                "using the deterministic selector fill"
            )
        driver = PlaywrightDriver(backend=llm_backend)
    # Prefer the cached full JD (e.g. Internshala's fetched description) over the short listing.
    row_dict = dict(row)
    job = Job.model_validate(
        {
            "job_uid": row["job_uid"],
            "source": row["source"],
            "title": row["title"],
            "company": row["company"],
            "url": row["url"],
            "location": row["location"],
            "description": row_dict.get("full_description") or row["description"],
            "is_remote": bool(row["is_remote"]) if row["is_remote"] is not None else None,
        }
    )
    # extra_context: the user's per-job notes / pasted posting (saved by the dashboard before it
    # spawned us). Feeds tailoring + the agent's field-fill. Read defensively — the column only
    # exists after the v3 migration, and a --db pointed at an old file may lack it.
    extra_context = dict(row).get("extra_context")
    result = apply_to_job(
        job,
        profile,
        cfg,
        driver=driver,
        backend=llm_backend,
        extra_context=extra_context,
    )
    # Mark applied only NOW — the fill completed and the human reviewed it in the browser.
    # (The dashboard deliberately does NOT set this on launch; a dead launch left phantom ✓s.)
    from job_aggregator.storage import jobs_repo

    jobs_repo.set_user_flag(conn, args.uid, "applied", True)
    print(f"ATS: {result.ats or 'generic'}  |  filled: {', '.join(result.filled) or 'none'}")
    print(f"unfilled: {', '.join(result.unfilled) or 'none'}  |  résumé: {result.resume_pdf}")
    print(f"preservation: {result.preservation:.0%}")
    for flag in result.flags:
        print(f"  ! {flag}")
    print("NOT auto-submitted — you reviewed and submitted it yourself in the browser.")
    print("Marked applied in the dashboard.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    from job_aggregator.paths import default_db_path

    # Shared options live on a parent parser so they are accepted AFTER the subcommand
    # (e.g. `job-aggregator initdb --db X`), matching the documented CLI usage.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=str(default_db_path()), help="path to the SQLite DB")
    common.add_argument("--log-level", default="INFO", help="logging level (default: INFO)")

    parser = argparse.ArgumentParser(
        prog="job-aggregator",
        description="Self-hosted multi-source job/internship aggregator.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("initdb", help="create and seed the database", parents=[common])
    p_init.set_defaults(func=cmd_initdb)

    p_run = sub.add_parser("run", help="execute one aggregation cycle now", parents=[common])
    p_run.set_defaults(func=cmd_run)

    p_serve = sub.add_parser("serve", help="launch the dashboard web app", parents=[common])
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="uvicorn autoreload (dev)")
    p_serve.set_defaults(func=cmd_serve)

    p_show = sub.add_parser("show-config", help="print the effective config", parents=[common])
    p_show.set_defaults(func=cmd_show_config)

    p_tailor = sub.add_parser(
        "tailor", help="tailor the résumé to one job by uid (Track D Step 0)", parents=[common]
    )
    p_tailor.add_argument("uid", help="job_uid to tailor for (from the dashboard/DB)")
    p_tailor.add_argument(
        "--llm",
        action="store_true",
        help="reword bullets via the configured backend (default: pure selection, no network)",
    )
    p_tailor.add_argument(
        "--out", default=None, help="output PDF path (default data/resumes/<uid>.pdf)"
    )
    p_tailor.set_defaults(func=cmd_tailor)

    p_apply = sub.add_parser(
        "apply",
        help="fill a job application in a headful browser to review + submit (Track D; local)",
        parents=[common],
    )
    p_apply.add_argument("uid", help="job_uid to apply for (from the dashboard/DB)")
    p_apply.add_argument(
        "--llm", action="store_true", help="reword résumé bullets via the configured backend"
    )
    p_apply.set_defaults(func=cmd_apply)

    return parser


def _load_env() -> None:
    """Load .env into os.environ if python-dotenv is present (secrets: Adzuna/Jooble/SMTP/...).
    Done AFTER parse_args so `--help`/`--version` stay stdlib-only."""
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _load_env()
    try:
        return int(args.func(args))
    except JobAggregatorError as exc:
        # Known application errors -> terse {code, message, details} envelope. Unexpected
        # exceptions propagate with a traceback (an honest bug failure).
        print(f"error [{exc.code.value}]: {exc.message}", file=sys.stderr)
        if exc.details:
            print(f"  details: {exc.details}", file=sys.stderr)
        return 1

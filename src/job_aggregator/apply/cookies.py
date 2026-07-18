"""Import login cookies from the user's own browser into the apply agent's Chromium (Track D).

WHY: the apply agent opens its own bundled Chromium, which starts logged OUT of everything —
so LinkedIn "Easy Apply" / Unstop "Quick Apply" walls appeared even though the user is logged
in all day in their real browser (Zen). Zen is Firefox-family: its cookies live in a plain
(unencrypted) `cookies.sqlite`, so we can copy the DB and hand the relevant rows to Playwright.

Scope + safety:
- Only cookies for the TARGET job posting's registrable domain (+ subdomains) are read — never
  the whole jar. Values are never logged.
- The DB is copied (with its -wal) to a temp dir before reading: the live file is locked while
  the browser runs, and we must never write to it.
- Best-effort by contract: any failure returns [] with a warning — a missing browser profile
  must never break an apply run (the agent then simply pauses for a manual login).
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Firefox-family profile roots searched in order; first match by most-recent cookie DB wins.
# Zen (the user's daily driver) keeps profiles under ~/.config/zen/<profile>/.
_PROFILE_GLOBS = (
    "~/.config/zen/*/cookies.sqlite",
    "~/.zen/*/cookies.sqlite",
    "~/.mozilla/firefox/*/cookies.sqlite",
    "~/.var/app/org.mozilla.firefox/.mozilla/firefox/*/cookies.sqlite",
)
# Firefox moz_cookies.sameSite -> Playwright sameSite. Firefox: 0=None, 1=Lax, 2=Strict.
_SAMESITE = {0: "None", 1: "Lax", 2: "Strict"}
# Values at/above this magnitude are epoch MILLISECONDS (same heuristic as pipeline.normalize):
# stock Firefox stores expiry in seconds, but Zen stores milliseconds (observed live 2026-07-18)
# — passed through as "seconds" they land in year ~58,000 and Playwright rejects the cookie,
# which used to kill the whole apply session at add_cookies.
_EPOCH_MS_THRESHOLD = 10**11
# Multi-label public suffixes where the registrable domain is THREE labels (foo.co.in). A tiny
# hand list beats a tldextract dependency for the handful of job domains this ever sees.
_SECOND_LEVEL_SUFFIXES = frozenset({"co", "com", "org", "net", "ac", "gov", "edu"})


def _expires_seconds(expiry: object) -> int:
    """A moz_cookies.expiry value -> Playwright `expires` (unix SECONDS, or -1 for session).

    Magnitude-detects Zen's millisecond timestamps and divides them down; anything absent,
    non-numeric, or non-positive maps to -1 (treat as a session cookie) — Playwright accepts
    only -1 or a sane positive number, and one bad cookie must never sink the import."""
    try:
        value = int(expiry)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return -1
    if value >= _EPOCH_MS_THRESHOLD:
        value //= 1000
    return value if value > 0 else -1


def base_domain(host: str) -> str:
    """The registrable domain of a hostname: www.linkedin.com -> linkedin.com,
    boards.greenhouse.io -> greenhouse.io, foo.co.in -> foo.co.in (3-label suffix aware)."""
    labels = [p for p in host.lower().strip(".").split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    if labels[-2] in _SECOND_LEVEL_SUFFIXES and len(labels[-1]) <= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def find_cookie_db(explicit: str | Path | None = None) -> Path | None:
    """The user's browser cookie DB: an explicit configured path, else the most recently
    modified match across known Firefox-family profile locations (Zen first)."""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    candidates: list[Path] = []
    for pattern in _PROFILE_GLOBS:
        base = Path(pattern).expanduser()
        candidates.extend(base.parent.parent.glob(f"{base.parent.name}/{base.name}"))
    live = [p for p in candidates if p.is_file()]
    return max(live, key=lambda p: p.stat().st_mtime) if live else None


def _copy_locked_db(db: Path, into: Path) -> Path:
    """Copy the cookie DB (+ its -wal journal, holding recent writes) so we can read while the
    browser holds the lock. Reading the copy is safe; the original is never opened."""
    target = into / "cookies.sqlite"
    shutil.copy2(db, target)
    wal = db.with_name(db.name + "-wal")
    if wal.exists():
        shutil.copy2(wal, into / (target.name + "-wal"))
    return target


def load_cookies_for_url(url: str, *, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Playwright-format cookies for the url's registrable domain (+ subdomains), or [].

    Best-effort: no profile / unreadable DB / no matching rows all return [] (with a log line
    saying why) — the apply flow then proceeds logged-out and pauses for a manual login.
    """
    host = urlparse(url).hostname or ""
    domain = base_domain(host)
    if not domain:
        return []
    db = find_cookie_db(db_path)
    if db is None:
        log.info("no browser cookie DB found; apply proceeds without imported logins")
        return []
    try:
        with tempfile.TemporaryDirectory(prefix="jobagg-cookies-") as tmp:
            copy = _copy_locked_db(db, Path(tmp))
            conn = sqlite3.connect(copy)
            try:
                rows = conn.execute(
                    "SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite "
                    "FROM moz_cookies WHERE host = ? OR host = ? OR host LIKE ?",
                    (domain, f".{domain}", f"%.{domain}"),
                ).fetchall()
            finally:
                conn.close()
    except (OSError, sqlite3.Error) as exc:
        log.warning("could not read browser cookies from %s: %s", db, exc)
        return []
    cookies = [
        {
            "name": name,
            "value": value,
            "domain": chost,
            "path": cpath or "/",
            "expires": _expires_seconds(expiry),
            "secure": bool(secure),
            "httpOnly": bool(http_only),
            "sameSite": _SAMESITE.get(same_site, "Lax"),
        }
        for name, value, chost, cpath, expiry, secure, http_only, same_site in rows
    ]
    log.info("imported %d %s cookie(s) from %s", len(cookies), domain, db.parent.name)
    return cookies

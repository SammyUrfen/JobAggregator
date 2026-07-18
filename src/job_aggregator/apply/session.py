"""Encrypted Playwright storageState persistence, per domain (Track D, opt-in).

You log in to a job site once; the browser session (cookies + localStorage) is saved so a later
apply to the same domain skips the login. The blob is Fernet-encrypted at rest — we never store raw
passwords, and the file is useless without JOBAGG_SESSION_KEY.

Caveats (documented, not bugs): Playwright storageState does NOT capture sessionStorage, and a
site's server-side token expiry still forces an occasional fresh login (surface "session expired").

`cryptography` ships in the `[apply]` extra and is imported lazily, so importing this module (and
running the rest of the test suite) never requires it. Generate a key once with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and export it as JOBAGG_SESSION_KEY.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from job_aggregator.errors import ConfigError
from job_aggregator.paths import sessions_dir

_SESSION_KEY_ENV = "JOBAGG_SESSION_KEY"
# Collapse anything that isn't a safe filename char so a domain can never traverse the FS.
_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def _safe_stem(domain: str) -> str:
    stem = _UNSAFE.sub("_", domain.strip().lower()).strip("._-")
    if not stem:
        raise ConfigError("empty session domain", details={"domain": domain})
    return stem


def _path(domain: str) -> Path:
    return sessions_dir() / f"{_safe_stem(domain)}.enc"


def _fernet() -> Any:
    """Build a Fernet from JOBAGG_SESSION_KEY. Lazy-imports cryptography (the `[apply]` extra)."""
    try:
        from cryptography.fernet import Fernet
    except ModuleNotFoundError as exc:  # pragma: no cover - only when [apply] is not installed
        raise ConfigError(
            "the apply extra is not installed; run: pip install -e '.[apply]'",
            details={"missing": "cryptography"},
        ) from exc
    key = os.environ.get(_SESSION_KEY_ENV)
    if not key:
        raise ConfigError(
            f"{_SESSION_KEY_ENV} is not set. Generate one once with:  python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            f"  then export {_SESSION_KEY_ENV}=<that value>.",
            details={"env": _SESSION_KEY_ENV},
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"{_SESSION_KEY_ENV} is not a valid Fernet key (32 url-safe base64 bytes)",
            details={"env": _SESSION_KEY_ENV},
        ) from exc


def has_state(domain: str) -> bool:
    """True if an encrypted session exists for this domain (no decryption attempted)."""
    return _path(domain).exists()


def save_state(domain: str, state: dict[str, Any]) -> None:
    """Encrypt + atomically write a Playwright storageState dict for this domain."""
    blob = _fernet().encrypt(json.dumps(state).encode())
    path = _path(domain)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(path)  # atomic swap so a crash mid-write never leaves a truncated blob


def load_state(domain: str) -> dict[str, Any] | None:
    """Decrypt + return this domain's storageState, or None if none is saved.

    A wrong key / corrupt blob raises ConfigError (a clear "regenerate the key / log in again"
    signal) rather than silently returning None, so a key mismatch never looks like "not logged in".
    """
    path = _path(domain)
    if not path.exists():
        return None
    from cryptography.fernet import InvalidToken

    try:
        raw = _fernet().decrypt(path.read_bytes())
    except InvalidToken as exc:
        raise ConfigError(
            f"cannot decrypt the saved session for {domain} — wrong {_SESSION_KEY_ENV} or corrupt "
            "file. Delete it and log in again.",
            details={"domain": domain},
        ) from exc
    result: dict[str, Any] = json.loads(raw)
    return result

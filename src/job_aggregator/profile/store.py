"""Load + validate the ground-truth profile from YAML (Track C).

Mirrors config/store.py's spirit: a YAML file the user maintains by hand, validated against a
Pydantic model on load so a typo fails loudly rather than silently corrupting a tailored résumé.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from job_aggregator.errors import ConfigError
from job_aggregator.paths import PROFILE_EXAMPLE_YAML, default_profile_path
from job_aggregator.profile.schema import Profile


def load_profile(path: str | Path | None = None) -> Profile:
    """Read + validate the profile YAML. Raises ConfigError with a friendly message if the file
    is missing or malformed (a missing profile is a setup step, not a crash)."""
    resolved = Path(path) if path is not None else default_profile_path()
    where: dict[str, object] = {"path": str(resolved)}
    if not resolved.exists():
        raise ConfigError(
            f"profile not found at {resolved} — "
            "copy config/profile.example.yaml to profile.yaml and fill it in before tailoring",
            details=where,
        )
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"profile YAML is invalid: {exc}", details=where) from exc
    try:
        return Profile.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError -> friendly envelope
        raise ConfigError(f"profile failed validation: {exc}", details=where) from exc


def load_profile_text(path: str | Path | None = None) -> str:
    """The raw profile YAML, for the dashboard editor. Returns the committed example as a starting
    template when no personal profile.yaml exists yet, so the editor is never blank."""
    resolved = Path(path) if path is not None else default_profile_path()
    if resolved.exists():
        return resolved.read_text(encoding="utf-8")
    return PROFILE_EXAMPLE_YAML.read_text(encoding="utf-8")


def save_profile_text(text: str, path: str | Path | None = None) -> Profile:
    """Validate raw YAML against the Profile model, then write it. Raises ConfigError (friendly) on
    a parse/validation failure — an invalid profile is NEVER persisted (it would silently corrupt a
    tailored résumé). On success the new profile takes effect immediately (load_profile re-reads).
    """
    resolved = Path(path) if path is not None else default_profile_path()
    where: dict[str, object] = {"path": str(resolved)}
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"profile YAML is invalid: {exc}", details=where) from exc
    try:
        profile = Profile.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError -> friendly envelope
        raise ConfigError(f"profile failed validation: {exc}", details=where) from exc
    resolved.parent.mkdir(parents=True, exist_ok=True)
    # tmp + replace: a crash mid-write must never leave a truncated profile.yaml (mirrors
    # apply/session.py's atomic blob write). os.replace fails across filesystems, hence a
    # sibling tmp file. NOTE: when profile.yaml is a Docker bind-MOUNTED file, replace() would
    # swap the mount point out from under the container — fall back to in-place write there.
    tmp = resolved.with_name(resolved.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(resolved)
    except OSError:  # bind-mounted single file (EBUSY/EXDEV) — validated content, write direct
        tmp.unlink(missing_ok=True)
        resolved.write_text(text, encoding="utf-8")
    return profile

"""Load + validate the ground-truth profile from YAML (Track C).

Mirrors config/store.py's spirit: a YAML file the user maintains by hand, validated against a
Pydantic model on load so a typo fails loudly rather than silently corrupting a tailored résumé.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from job_aggregator.errors import ConfigError
from job_aggregator.paths import default_profile_path
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

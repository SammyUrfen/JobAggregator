"""Track D — apply/session: Fernet-encrypted storageState round-trip.

Skipped unless the `[apply]` extra (cryptography) is installed — mirrors the opt-in nature of the
apply agent; runs in full once you `pip install -e '.[apply]'`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from cryptography.fernet import Fernet

from job_aggregator.apply import session
from job_aggregator.errors import ConfigError


def test_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOBAGG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JOBAGG_SESSION_KEY", Fernet.generate_key().decode())
    assert session.has_state("greenhouse.io") is False
    assert session.load_state("greenhouse.io") is None
    state = {"cookies": [{"name": "sid", "value": "x"}], "origins": []}
    session.save_state("greenhouse.io", state)
    assert session.has_state("greenhouse.io") is True
    assert session.load_state("greenhouse.io") == state


def test_wrong_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOBAGG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JOBAGG_SESSION_KEY", Fernet.generate_key().decode())
    session.save_state("acme.com", {"cookies": []})
    monkeypatch.setenv("JOBAGG_SESSION_KEY", Fernet.generate_key().decode())  # different key
    with pytest.raises(ConfigError):
        session.load_state("acme.com")


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOBAGG_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("JOBAGG_SESSION_KEY", raising=False)
    with pytest.raises(ConfigError):
        session.save_state("x.com", {"cookies": []})

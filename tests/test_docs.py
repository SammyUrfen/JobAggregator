"""Phase 9 — docs hygiene: required docs exist, are non-empty, and use the real commands."""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_REQUIRED_DOCS = ["README.md", "docs/ats_token_lists.md", "docs/testing.md", "TROUBLESHOOTING.md"]


@pytest.mark.parametrize("rel", _REQUIRED_DOCS)
def test_required_doc_exists_and_nonempty(rel: str) -> None:
    path = _REPO / rel
    assert path.exists(), f"missing required doc: {rel}"
    assert len(path.read_text(encoding="utf-8").strip()) > 200, f"doc is too short: {rel}"


def test_readme_quickstart_uses_real_subcommands() -> None:
    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    for sub in ("initdb", "run", "serve", "show-config"):
        assert sub in readme, f"README quickstart missing subcommand: {sub}"
    # zsh eats an unquoted .[dev]; the README must show the quoted form.
    assert "pip install -e '.[dev]'" in readme


def test_readme_is_no_longer_scaffold() -> None:
    assert "scaffold" not in (_REPO / "README.md").read_text(encoding="utf-8").lower()

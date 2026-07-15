"""Phase 9 — docs/repo hygiene.

README quickstart commands exist; ruff/mypy/pytest gates documented; no stray
TODOs left as impl.

See PLAN.md Part II (Phase 9) for the exact cases to implement.
"""

import pytest

pytestmark = pytest.mark.skip(reason="Phase 9: not yet implemented (scaffold placeholder)")


def test_placeholder() -> None:
    raise NotImplementedError("Phase 9")

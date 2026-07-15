# Testing (Phase 9)

- Runner: `pytest` (config in pyproject; `pythonpath=src`).
- HTTP sources are mocked with **respx** against recorded fixtures in `tests/fixtures/`.
- Time is injected via **FixedClock** (see `tests/conftest.py`) — no wall-clock in tests.
- The correctness core (dedup, salary, filters, stale, runner) is tested hardest; aim for
  high coverage there specifically.
- Per-phase test lists live in PLAN.md Part II. Each phase's Acceptance check names the
  exact command that must pass.

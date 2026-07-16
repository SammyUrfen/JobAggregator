# Testing

How the JobAggregator test suite is laid out, how to run it, and the three house rules that
keep it deterministic. The suite is **table-driven, deterministic, and offline by default** —
no test touches the network or the wall clock unless it explicitly opts in.

## Test layout

`tests/` mirrors the behavior of the package rather than its file tree — one module per
concern, named for what it verifies:

| Area | Modules |
|---|---|
| Storage (Phase 1) | `test_jobs_repo.py`, `test_config_store.py` |
| Pure pipeline logic (Phase 2) | `test_dedup.py`, `test_salary.py`, `test_filters.py`, `test_normalize.py`, `test_stale.py` |
| Sources via `respx` (Phase 3) | `test_sources_apis.py`, `test_sources_ats.py`, `test_jobspy_source.py` |
| Runner + orchestration (Phase 5) | `test_runner.py` |
| Notify (Phase 7) | `test_notify.py` |
| Scheduler / CLI / dashboard (Phases 6, 8) | `test_scheduler.py`, `test_cli.py`, `test_dashboard.py` |
| Offline end-to-end (Phase 9) | `test_e2e_offline.py` |

Supporting material lives alongside the tests:

- **`tests/fixtures/`** — golden JSON captures of real upstream responses (one file per
  adapter shape: `greenhouse.json`, `lever.json`, `ashby.json`, `smartrecruiters.json`,
  `adzuna.json`, `jooble.json`, `remoteok.json`, `himalayas.json`, `jobicy.json`, `unstop.json`,
  plus edge captures like `lever_notfound.json`). These pin the exact response shapes the
  adapters parse; served through `respx`, they let the source tests run offline.
- **`tests/_fakes.py`** — shared test doubles, **imported not collected** (the leading
  underscore keeps pytest from treating it as a test module):
  - `make_job(uid, *, source=..., title=..., ...)` — builds a normalized `Job` crafted to pass
    the permissive `sample_config`. Same `uid` across two jobs collapses under dedup; for
    genuinely distinct jobs use different uids **and** dissimilar title+company.
  - `FakeSource(name, jobs, *, succeeded=True, error=None, sub_results=...)` — a `Source` that
    returns a canned `SourceResult` with no network.
  - `RaisingSource(name)` — a `Source` whose `fetch()` raises, to exercise the runner's
    belt-and-suspenders guard.
  - `RecordingNotifier` — a duck-typed notifier that records the uids delivered on each
    `notify_new` call (it does **not** import `notify.base`, so Phase 5 never depends forward
    on Phase 7).
- **`tests/conftest.py`** — the shared fixtures (see the table under "conftest fixtures").

## Running the suite

The coverage gate is **baked into `addopts`** in `pyproject.toml`, so plain `pytest` runs the
whole suite under coverage and fails if the overall floor is missed:

```
addopts = "-q --strict-markers --cov=job_aggregator --cov-report=term-missing --cov-fail-under=85"
```

```bash
# whole suite, with the coverage gate (what CI / "done" means)
pytest

# a fast subset while iterating — turn coverage off so the 85% gate doesn't fail on one file
pytest --no-cov tests/test_dedup.py
pytest --no-cov tests/test_runner.py -k stale

# opt-in markers (both are deselected by default via --strict-markers)
pytest -m network     # real network I/O — hits live upstreams; run deliberately
pytest -m slow        # slower integration tests (e2e, full run_cycle)

# an HTML coverage report to browse line-by-line
pytest --cov-report=html   # writes htmlcov/index.html
```

`--strict-markers` means an unregistered marker is an error, not a silent skip. Only two
markers are registered: `network` (real network I/O; deselected by default) and `slow`
(slower integration test — e2e, full `run_cycle`).

## The three house rules

These are what make the suite deterministic. They are enforced by convention and by the
fixtures below; break them and tests become flaky.

### (a) Inject a `FixedClock` — never call `datetime.now()` in code under test

Every time-dependent path takes a `Clock`. Tests inject a `FixedClock` anchored at a known
instant and `advance()` it explicitly; nothing reads the wall clock.

```python
def test_grace_window_stale_to_deleted_full_cycle(conn, clock, sample_config):
    run_cycle(conn, sample_config, clock, "manual",
              sources=[FakeSource("X", [make_job("x", source="X")])], notifiers=[])
    run_cycle(conn, sample_config, clock, "manual",
              sources=[FakeSource("X", [])], notifiers=[])          # X ok but empty -> x stale
    clock.advance(days=sample_config.schedule.grace_days + 1)
    run_cycle(conn, sample_config, clock, "manual",
              sources=[FakeSource("X", [])], notifiers=[])          # -> deleted
    assert _row(conn, "x")["status"] == "deleted"
```

### (b) `respx.mock` for ALL HTTP

No test opens a real socket (unless marked `network`). Source adapters are driven through
`respx`, which intercepts `httpx` and replies from a golden fixture:

```python
def test_greenhouse_maps_and_company_fallback(load_fixture, now_clock, cfg):
    with respx.mock:
        respx.route(method="GET", host="boards-api.greenhouse.io").mock(
            return_value=httpx.Response(200, json=load_fixture("greenhouse.json"))
        )
        res = GreenhouseSource(tokens=["acme"]).fetch(cfg, now_clock)
    assert res.succeeded is True
```

### (c) Golden fixtures pin adapter response shapes

The JSON in `tests/fixtures/` is the contract for what each upstream returns. Adapter tests
assert the mapping from that exact shape to a normalized `Job` (remote inference, company
fallback, salary currency/period, epoch-ms → datetime, per-company partial-success). When an
upstream changes its schema, re-capture the fixture — the diff is the review.

## The deterministic run-cycle harness

The runner is tested end-to-end without network or wall clock by combining **`FakeSource` +
`FixedClock`**: fake sources supply exactly the jobs a test wants, and advancing the clock
past `grace_days` drives the full **active → stale → deleted** lifecycle deterministically.

`tests/test_runner.py` covers the correctness core of `run_cycle` in isolation — cross-source
dedup provenance, the guarded stale-delete (only sources that *succeeded this cycle* can stale
their own jobs), per-subsource guards for `jobspy`, user-flag preservation across cycles,
notify-new-only, and the `success` / `partial` / `failed` status matrix.

`tests/test_e2e_offline.py` (marked `slow`) runs the **real dispatcher path** — it
monkeypatches `registry.build_enabled_sources` rather than injecting sources directly — and
walks a full lifecycle across four cycles:

```python
# cycle 3: source now empty (succeeded) -> jobs go stale but stay within grace.
clock.advance(days=sample_config.schedule.grace_days - 1)
run_cycle(conn, sample_config, clock, "manual", notifiers=[])
assert _visible(conn) == 2                       # stale, not yet deleted

# cycle 4: past the grace boundary -> deleted (gone from the default view).
clock.advance(days=2)
s4 = run_cycle(conn, sample_config, clock, "manual", notifiers=[])
assert s4.n_expired > 0
assert _visible(conn) == 0
```

The grace boundary is the load-bearing assertion: advancing to `grace_days - 1` must leave
jobs stale-but-visible; crossing it must delete them. Because the clock is injected, this is
exact and reproducible — no `sleep`, no tolerance windows.

## Coverage targets

Two distinct bars:

- **Overall hard gate: 85%**, enforced in `addopts` via `--cov-fail-under=85`. The thin,
  network-bound, or I/O-shell modules are omitted from the measured base so their noisy line
  coverage can't mask the correctness core — `[tool.coverage.run].omit` drops
  `*/__main__.py`, `*/cli.py`, `*/dashboard/*`, and `*/logging_setup.py`. Those shells are
  still exercised (by `respx`-mocked and `TestClient` tests), just not counted toward the
  gate.
- **Correctness core: ≥90%**, a stricter self-imposed bar on the modules where a bug actually
  costs data. Check it directly after a run:

  ```bash
  coverage report --include="*/storage/*,*/pipeline/*,*/config/*,*/models/*"
  ```

  Storage, pipeline (dedup / salary / filters / normalize / stale / runner), config, and
  models are the parts that must not silently regress.

## conftest fixtures

Everything is anchored to one of two fixed instants — `FIXED_INSTANT = 2026-01-01 UTC`
(storage/logic tests) and `FIXED_NOW = 2026-07-15 12:00 UTC` (source recency tests).

| Fixture | What it gives you |
|---|---|
| `fixed_clock` | `FixedClock` at `FIXED_INSTANT`; `advance()` in tests. |
| `clock` | Phase 1+ alias: a fresh `FixedClock` at `FIXED_INSTANT`. |
| `now_clock` | `FixedClock` at `FIXED_NOW`, for Phase 3 source recency filters. |
| `conn` | A fresh initialized on-disk SQLite DB (WAL) — the storage-test connection. |
| `db` | Same idea (fresh initialized WAL DB); the Phase 0 name. |
| `run_id` | A started (`status='running'`) manual run whose id jobs can reference as `last_seen_cycle`. |
| `make_job` | Factory building a normalized `Job` with sensible defaults and a unique `job_uid` per call; pass keyword overrides. |
| `cfg` | The default seed config as a validated `Config`. |
| `sample_config` | A **permissive** config for runner/stale tests: `require_level=False`, `on_missing="keep_and_flag"`, `grace_days=3`. |
| `fx_rates` | The default approximate FX table (INR per unit: USD 83, EUR 90, GBP 105) for salary tests. |
| `load_fixture` | Loads a golden JSON capture from `tests/fixtures/` by filename. |

> Note the two `make_job` helpers. `conftest.make_job` (fixture) takes overrides as keyword
> args with an auto-incrementing `job_uid`; `_fakes.make_job` (import) takes an explicit `uid`
> positionally and is tuned to pass `sample_config`. Runner/stale tests use the `_fakes`
> version; unit tests use the fixture.

## Limitations

- **The gate is line/branch coverage, not correctness.** 85%/90% says code *ran*, not that
  its behavior is right. The value is in the table-driven assertions, not the percentage.
- **`-m network` tests hit live upstreams** and are excluded from the default run for a
  reason: they are non-deterministic (rate limits, schema drift, outages) and are not part of
  the "done" gate. A green `pytest` says nothing about whether real endpoints still work.
- **Golden fixtures are point-in-time captures** (verified 2026-07-14 per `research.md`). They
  guarantee the adapter parses *that* shape; they do **not** detect that an upstream has since
  changed its schema. Only a `network` run catches drift.
- **The dashboard/CLI shells are omitted from the coverage base**, so a regression in those
  layers won't move the gate. They're covered by `TestClient`/CLI tests, but the numeric bar
  does not protect them — the Playwright-driven manual checks in CLAUDE.md's "Safety when
  verifying the dashboard" fill that gap.
- **`freezegun` is available** for the rare spot where injecting a `FixedClock` is awkward, but
  injection is the default and preferred path; reach for `freezegun` only when there's no seam
  to pass a clock through.

"""Track + stop spawned apply subprocesses so the dashboard has a kill switch (Track D).

An apply run is a DETACHED process the dashboard spawns; the review-gate holds it open until
the human submits + closes the browser. Without a registry that meant a haywire or stuck agent
(e.g. a site that keeps bouncing to a login) had no off switch and piled up orphans.

Design:
- Each launch registers its PID here. The process is spawned in its OWN session
  (`start_new_session=True`), so PID == process-group id and one killpg takes down the whole
  tree (python + claude + npx + chromium) — WITHOUT touching the serve process that spawned it
  (which the old plain-Popen shared a group with, making a group-kill unsafe).
- `stop_all` SIGTERMs each group, waits a grace period, then SIGKILLs survivors.
- Liveness + identity are re-checked from /proc every time, so a reused PID is never signalled:
  we only kill a PID whose cmdline is still a `job_aggregator apply`.
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

_PIDS_FILENAME = "apply_pids"


def _pids_file() -> Path:
    from job_aggregator.paths import data_dir

    return data_dir() / _PIDS_FILENAME


def _is_apply_proc(pid: int) -> bool:
    """True if PID is alive AND its cmdline is one of our apply runs. The /proc cmdline check
    guards against a recycled PID — we must never killpg an unrelated process that happens to
    have reused the number. Non-Linux / unreadable /proc -> False (fail safe: don't signal)."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    cmd = raw.replace(b"\0", b" ").decode(errors="replace")
    return "job_aggregator" in cmd and " apply" in f" {cmd}"


def register_pid(pid: int, *, path: Path | None = None) -> None:
    """Record a freshly-spawned apply PID (append; the file is a set-like log pruned on read)."""
    p = path or _pids_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(f"{pid}\n")


def _read_pids(path: Path) -> list[int]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[int] = []
    for tok in text.split():
        if tok.isdigit():
            out.append(int(tok))
    return out


def live_pids(*, path: Path | None = None) -> list[int]:
    """The registered PIDs that are still live apply runs, de-duped and in registration order.
    Prunes the file to just those (dead/finished runs drop out)."""
    p = path or _pids_file()
    live = [pid for pid in dict.fromkeys(_read_pids(p)) if _is_apply_proc(pid)]
    try:
        p.write_text("".join(f"{pid}\n" for pid in live), encoding="utf-8")
    except OSError:
        pass
    return live


def _signal_group(pid: int, sig: int) -> None:
    """Signal the PID's whole process group (python + claude + npx + chromium), falling back to
    the bare PID if the group lookup fails. Never raises."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def stop_all(
    *, path: Path | None = None, grace_s: float = 2.0, sleep: Callable[[float], None] = time.sleep
) -> int:
    """Stop every live apply run: SIGTERM each group, wait `grace_s`, SIGKILL any survivor.
    Returns how many runs were signalled. Clears the registry."""
    p = path or _pids_file()
    pids = live_pids(path=p)
    for pid in pids:
        _signal_group(pid, signal.SIGTERM)
    if pids:
        sleep(grace_s)
    for pid in pids:
        if _is_apply_proc(pid):  # ignored the TERM (stuck in a syscall) -> hard kill the group
            _signal_group(pid, signal.SIGKILL)
    try:
        p.write_text("", encoding="utf-8")
    except OSError:
        pass
    return len(pids)

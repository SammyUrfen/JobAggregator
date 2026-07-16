"""Central error hierarchy + error codes.

Mirrors the pattern used across the user's other projects: a custom exception hierarchy →
one place translates to an HTTP `{code, message, details}` envelope (done in the dashboard,
Phase 8). Sources must NOT let these escape `fetch()` — they convert failures into
`SourceResult(succeeded=False, error=...)` so the run cycle stays alive (see PLAN §3, §4.1).
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable machine-readable codes for the API error envelope."""

    CONFIG_INVALID = "config_invalid"
    STORAGE_ERROR = "storage_error"
    SOURCE_FETCH_FAILED = "source_fetch_failed"
    SOURCE_PARSE_FAILED = "source_parse_failed"
    NOTIFY_FAILED = "notify_failed"
    RUN_IN_PROGRESS = "run_in_progress"
    NOT_FOUND = "not_found"
    AGENT_FAILED = "agent_failed"
    RENDER_FAILED = "render_failed"
    INTERNAL = "internal"


class JobAggregatorError(Exception):
    """Base class for all application errors."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigError(JobAggregatorError):
    code = ErrorCode.CONFIG_INVALID


class StorageError(JobAggregatorError):
    code = ErrorCode.STORAGE_ERROR


class SourceError(JobAggregatorError):
    """Raised inside a source adapter; caught by the adapter and turned into a
    failed SourceResult. Never propagated out of Source.fetch()."""

    code = ErrorCode.SOURCE_FETCH_FAILED


class NotifyError(JobAggregatorError):
    code = ErrorCode.NOTIFY_FAILED


class RunInProgressError(JobAggregatorError):
    code = ErrorCode.RUN_IN_PROGRESS


class NotFoundError(JobAggregatorError):
    """A requested entity (job, run, config) does not exist. Maps to HTTP 404."""

    code = ErrorCode.NOT_FOUND


class AgentError(JobAggregatorError):
    """An LLM/agent backend call failed (HTTP error, non-zero exit, empty output). Callers should
    degrade gracefully — tailoring falls back to the untailored profile, never a hard crash."""

    code = ErrorCode.AGENT_FAILED


class RenderError(JobAggregatorError):
    """LaTeX -> PDF compilation failed or no engine is installed."""

    code = ErrorCode.RENDER_FAILED

"""Pluggable LLM/agent backends (Track D).

One primitive — `AgentBackend.complete(system, user)` — with two implementations chosen in config:

- **OpenAICompatibleBackend**: POST /chat/completions to any OpenAI-compatible `base_url` (OpenAI,
  Groq, Gemini's OpenAI shim, a local vLLM/Ollama, …). The key comes from the env var named in
  config — never the config row itself.
- **CodingAgentBackend**: pipe the prompt into a local coding-agent CLI (e.g. `claude -p`) and read
  stdout — so the user can drive tailoring with Claude Code / Codex and pay nothing extra.

Both raise AgentError on failure so callers can degrade (résumé tailoring falls back to untailored
facts rather than crashing a flow).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx

from job_aggregator.errors import AgentError, ConfigError

if TYPE_CHECKING:
    from job_aggregator.config.schema import ResumeConfig

_HTTP_TIMEOUT_S = 60.0
_AGENT_TIMEOUT_S = 180.0  # a local coding agent can be slow; generous but bounded


class AgentBackend(Protocol):
    """Text-in, text-out completion. Implementations MUST raise AgentError (not leak transport
    exceptions) so tailoring's fallback logic is simple."""

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str: ...


class OpenAICompatibleBackend:
    def __init__(
        self, base_url: str, model: str, api_key: str, *, timeout: float = _HTTP_TIMEOUT_S
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._key = api_key
        self._timeout = timeout

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        try:
            resp = httpx.post(
                self._url,
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "model": self._model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
            raise AgentError(f"openai-compatible backend failed: {exc}") from exc
        return str(content).strip()


class CodingAgentBackend:
    """Drive a local coding agent as a subprocess: it receives `system\\n\\nuser` on stdin and its
    stdout is the completion. `command` is the user's own config (single-user, localhost), run
    without a shell.

    For a `claude` command the session is made LEAN — this is a stateless text completion, so no
    MCP servers (the user's serena/playwright/… each spawn a subprocess for nothing here) and no
    tools. Those flags are appended only for claude; any other agent runs verbatim."""

    # Appended for a claude command: --strict-mcp-config with no --mcp-config loads ZERO MCP
    # servers; --disallowedTools shuts off every built-in tool (pure text-in/text-out).
    _CLAUDE_LEAN_FLAGS = (
        "--strict-mcp-config",
        "--disallowedTools",
        "Bash,Edit,Write,Read,Glob,Grep,Task,WebFetch,WebSearch,NotebookEdit,TodoWrite",
    )

    def __init__(self, command: list[str], *, timeout: float = _AGENT_TIMEOUT_S) -> None:
        if not command:
            raise ConfigError("resume.agent_command is empty")
        self._command = command
        self._timeout = timeout

    def _effective_command(self) -> list[str]:
        """The command plus lean-session flags when it's a claude invocation (idempotent — skips
        if the user already set --strict-mcp-config)."""
        cmd = list(self._command)
        is_claude = bool(cmd) and Path(cmd[0]).name == "claude"
        if is_claude and "--strict-mcp-config" not in cmd:
            cmd += self._CLAUDE_LEAN_FLAGS
        return cmd

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        prompt = f"{system}\n\n{user}"
        try:
            proc = subprocess.run(
                self._effective_command(),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AgentError(f"coding agent {self._command!r} failed to run: {exc}") from exc
        if proc.returncode != 0:
            raise AgentError(f"coding agent exited {proc.returncode}: {proc.stderr.strip()[:200]}")
        out = proc.stdout.strip()
        if not out:
            raise AgentError("coding agent returned empty output")
        return out


def build_backend(cfg: ResumeConfig) -> AgentBackend:
    """Construct the configured backend. Raises ConfigError if the OpenAI-compatible key env var
    is unset (so the failure is a friendly setup message, not a 401 mid-tailoring)."""
    if cfg.backend == "coding_agent":
        return CodingAgentBackend(cfg.agent_command)
    key = os.environ.get(cfg.api_key_env, "")
    if not key:
        raise ConfigError(
            f"résumé backend needs an API key in ${cfg.api_key_env}",
            details={"env": cfg.api_key_env},
        )
    return OpenAICompatibleBackend(cfg.base_url, cfg.model, key)


def try_build_backend(cfg: ResumeConfig) -> AgentBackend | None:
    """build_backend, but returns None instead of a broken/raising backend when it can't actually
    run — a missing OpenAI key, or a coding-agent CLI (`claude`) not on PATH. Callers use this so
    a missing tool degrades to deterministic tailoring rather than crashing the flow."""
    try:
        backend = build_backend(cfg)
    except ConfigError:
        return None
    if cfg.backend == "coding_agent":
        exe = (cfg.agent_command or ["claude"])[0]
        if not shutil.which(exe):
            return None
    return backend

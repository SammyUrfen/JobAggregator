"""Track D — agent backends: OpenAI-compatible HTTP + local coding-agent subprocess."""

from __future__ import annotations

import httpx
import pytest
import respx

from job_aggregator.apply.backends import (
    CodingAgentBackend,
    OpenAICompatibleBackend,
    build_backend,
)
from job_aggregator.config.schema import ResumeConfig
from job_aggregator.errors import AgentError, ConfigError


def test_openai_backend_posts_and_parses() -> None:
    with respx.mock:
        route = respx.post("https://api.x/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": " hi "}}]})
        )
        out = OpenAICompatibleBackend("https://api.x/v1", "m", "KEY").complete("sys", "usr")
    assert out == "hi"  # stripped
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer KEY"


def test_openai_backend_http_error_raises_agenterror() -> None:
    with respx.mock:
        respx.post("https://api.x/v1/chat/completions").mock(return_value=httpx.Response(500))
        with pytest.raises(AgentError):
            OpenAICompatibleBackend("https://api.x/v1", "m", "K").complete("s", "u")


def test_openai_backend_malformed_json_raises_agenterror() -> None:
    with respx.mock:
        respx.post("https://api.x/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"unexpected": 1})
        )
        with pytest.raises(AgentError):
            OpenAICompatibleBackend("https://api.x/v1", "m", "K").complete("s", "u")


def test_coding_agent_pipes_prompt_through_stdin() -> None:
    # `cat` echoes stdin, so the completion == the composed prompt (system + user).
    out = CodingAgentBackend(["cat"]).complete("SYSTEM", "USER")
    assert "SYSTEM" in out and "USER" in out


def test_coding_agent_nonzero_exit_raises() -> None:
    with pytest.raises(AgentError):
        CodingAgentBackend(["false"]).complete("s", "u")


def test_coding_agent_empty_output_raises() -> None:
    with pytest.raises(AgentError):
        CodingAgentBackend(["true"]).complete("s", "u")  # exits 0 but prints nothing


def test_coding_agent_empty_command_is_configerror() -> None:
    with pytest.raises(ConfigError):
        CodingAgentBackend([])


def test_coding_agent_claude_gets_lean_session_flags() -> None:
    # A claude command is made lean: no MCP servers, no tools (a stateless text completion).
    cmd = CodingAgentBackend(["claude", "-p", "--model", "sonnet"])._effective_command()
    assert "--strict-mcp-config" in cmd  # loads ZERO MCP servers (no serena/playwright spawn)
    assert "--disallowedTools" in cmd  # no built-in tools
    assert cmd[:2] == ["claude", "-p"]  # user's command preserved, flags appended


def test_coding_agent_lean_flags_are_idempotent() -> None:
    # If the user already set --strict-mcp-config, don't double-append.
    base = ["claude", "-p", "--strict-mcp-config"]
    assert CodingAgentBackend(base)._effective_command() == base


def test_coding_agent_non_claude_command_unchanged() -> None:
    # A different agent (or `cat` in tests) runs verbatim — the claude-only flags aren't added.
    assert CodingAgentBackend(["cat"])._effective_command() == ["cat"]
    assert CodingAgentBackend(["codex", "exec"])._effective_command() == ["codex", "exec"]


def test_build_backend_coding_agent() -> None:
    cfg = ResumeConfig(backend="coding_agent", agent_command=["cat"])
    assert isinstance(build_backend(cfg), CodingAgentBackend)


def test_build_backend_openai_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="API key"):
        build_backend(ResumeConfig(backend="openai_compatible"))


def test_build_backend_openai_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    # default backend is now coding_agent — request the OpenAI one explicitly
    assert isinstance(
        build_backend(ResumeConfig(backend="openai_compatible")), OpenAICompatibleBackend
    )

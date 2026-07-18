"""Track D — apply/grounding: LLM field-mapping via a fake backend (no browser)."""

from __future__ import annotations

from typing import Any

import pytest

from job_aggregator.apply.detector import Field
from job_aggregator.apply.driver import ApplicationFields
from job_aggregator.apply.grounding import _parse_plan, plan_fills


class _FakeBackend:
    def __init__(self, out: str) -> None:
        self.out = out
        self.prompts: list[str] = []

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.prompts.append(user)
        return self.out


def _fields() -> list[Field]:
    return [
        Field(0, "input", "text", "name", "Full Name", 10, 10, "", False),
        Field(1, "input", "email", "email", "Email", 10, 30, "", False),
        Field(2, "input", "text", "company", "Company", 10, 50, "Acme", False),  # already filled
        Field(3, "input", "file", "resume", "Résumé", 10, 70, "", True),  # file input
        Field(4, "select", "select", "country", "Country", 10, 90, "", False),  # native select
    ]


def _app() -> ApplicationFields:
    return ApplicationFields(
        full_name="Bibek Charah",
        first_name="Bibek",
        last_name="Charah",
        email="b@x.com",
        resume_path="/r.pdf",
    )


def test_plan_fills_maps_only_empty_text_fields() -> None:
    be = _FakeBackend('{"0": "Bibek Charah", "1": "b@x.com", "9": "hallucinated"}')
    plan = plan_fills(_fields(), _app(), be)
    assert plan == {0: "Bibek Charah", 1: "b@x.com"}  # index 9 (not a real field) is dropped
    prompt = be.prompts[0]
    assert "Full Name" in prompt and "Email" in prompt
    assert "Company" not in prompt  # already filled -> not offered
    assert "Résumé" not in prompt and "Country" not in prompt  # file + select excluded


def test_plan_fills_skips_backend_when_no_empty_fields() -> None:
    filled_only = [Field(0, "input", "text", "n", "Name", 1, 1, "already", False)]
    be = _FakeBackend('{"0": "x"}')
    assert plan_fills(filled_only, _app(), be) == {}
    assert be.prompts == []  # backend not even called


def test_plan_fills_degrades_on_bad_reply() -> None:
    assert plan_fills(_fields(), _app(), _FakeBackend("sorry, I can't")) == {}


def test_plan_fills_degrades_on_backend_error() -> None:
    class Boom:
        def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
            raise RuntimeError("down")

    assert plan_fills(_fields(), _app(), Boom()) == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"0": "a", "1": "b"}', {0: "a", 1: "b"}),
        ('```json\n{"0": "a"}\n```', {0: "a"}),
        ('here you go: {"0": "a", "2": "c"} done', {0: "a", 2: "c"}),
        ('{"0": "", "1": "  "}', {}),  # empty/whitespace values dropped
        ("not json at all", {}),
        ('{"5": "x"}', {}),  # index not in the valid set
    ],
)
def test_parse_plan(raw: str, expected: dict[int, str]) -> None:
    assert _parse_plan(raw, valid={0, 1, 2}) == expected


def test_field_describe_and_flags() -> None:
    empty = Field(0, "input", "text", "n", "Name", 1, 1, "", False)
    filled = Field(1, "input", "text", "e", "Email", 1, 1, "x@y.com", False)
    a_file: Any = Field(2, "input", "file", "r", "Résumé", 1, 1, "", True)
    assert "EMPTY" in empty.describe() and "[0]" in empty.describe()
    assert "x@y.com" in filled.describe()
    assert empty.is_text and not empty.filled
    assert filled.filled
    assert not a_file.is_text  # file inputs are excluded from text grounding

"""Phase 2 — pipeline.dedup.

norm_company/title/location, content_hash stability, canonical_url tracking-param
stripping, fuzzy_is_dup.

See PLAN.md Part II (Phase 2) for the exact table-driven cases to implement.
"""

from __future__ import annotations

import pytest

from job_aggregator.pipeline import dedup


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Stripe, Inc.", "stripe"),
        ("Acme Technologies Pvt Ltd", "acme"),
        ("Razorpay Software Private Limited", "razorpay"),
        ("Systems Ltd", "systems ltd"),  # all-suffix guard: keep originals, never empty
    ],
)
def test_norm_company(raw: str, expected: str) -> None:
    assert dedup.norm_company(raw) == expected


def test_norm_title_collapses_and_folds() -> None:
    assert dedup.norm_title("  Señior  Backend/Engineer  ") == "senior backend engineer"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Bangalore", "bengaluru"),
        ("Bengaluru, India", "bengaluru india"),
        ("Worldwide", "remote"),
        ("WFH", "remote"),
        (None, ""),
        ("", ""),
    ],
)
def test_norm_location(raw: str | None, expected: str) -> None:
    assert dedup.norm_location(raw) == expected


def test_content_hash_is_64_hex_and_deterministic() -> None:
    h = dedup.content_hash("Stripe, Inc.", "Backend Intern", "Bengaluru")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    assert h == dedup.content_hash("Stripe", "backend  intern", "bengaluru")  # normalized-equal


def test_content_hash_is_location_sensitive() -> None:
    a = dedup.content_hash("Acme", "Backend Intern", "Bengaluru")
    b = dedup.content_hash("Acme", "Backend Intern", "Remote")
    assert a != b


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://JOBS.Example.com/Careers/Eng-42?utm_source=li&gh_jid=99",
            "https://jobs.example.com/Careers/Eng-42?gh_jid=99",
        ),
        ("https://x.com/a/?utm_medium=x", "https://x.com/a"),  # trailing slash + utm dropped
        ("", ""),
        ("not a url", "not a url"),  # bare/relative string returned as-is
        ("javascript:alert(document.domain)", ""),  # XSS scheme blocked
        ("JavaScript:alert(1)", ""),  # case-insensitive scheme block
        ("data:text/html,<script>alert(1)</script>", ""),
    ],
)
def test_canonical_url(url: str, expected: str) -> None:
    assert dedup.canonical_url(url) == expected


def test_fuzzy_is_dup() -> None:
    assert dedup.fuzzy_is_dup("backend engineer intern", "backend engineer internship") is True
    assert dedup.fuzzy_is_dup("backend engineer intern", "graphic designer intern") is False

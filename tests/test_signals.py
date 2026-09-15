"""Text signals: work mode, stated pay, internship duration. Cases come from real postings seen in
the 2026-09-15 audit, plus the false matches each pattern must not make."""

from __future__ import annotations

import pytest

from job_aggregator.pipeline import signals


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("Location: Bengaluru (Remote)", "remote"),
        ("This is a fully remote role", "remote"),
        ("Work from home", "remote"),
        ("Hybrid / Remote", "remote_or_hybrid"),
        ("Work mode: Hybrid (3 days/week in office)", "hybrid"),  # office days make it hybrid
        ("Work mode: Hybrid, Bangalore", "hybrid"),
        ("onsite internship (No remote options)", "onsite"),
        ("Bangalore - Church Street (in-office). ❌ no remote", "onsite"),
        ("Work Location: In person", "onsite"),
        # technical phrases name no work mode
        ("Build remote diagnostics for our fleet", None),
        ("Hybrid retrieval over a vector store", None),
        ("Onsite interview in the last round", None),
        ("GIS remote sensing platform. Location: Pune", None),
        ("Help users via remote desktop", None),
        ("Software Engineer II (India, Remote)", "remote"),
        ("", None),
        (None, None),
    ],
)
def test_work_mode(text: str | None, mode: str | None) -> None:
    assert signals.work_mode(text) == mode


def test_remote_flag() -> None:
    assert signals.remote_flag("remote") is True
    assert signals.remote_flag("remote_or_hybrid") is True
    assert signals.remote_flag("hybrid") is False
    assert signals.remote_flag("onsite") is False
    assert signals.remote_flag(None) is None


@pytest.mark.parametrize(
    ("text", "pay"),
    [
        ("Internship Type: Unpaid", 0),
        ("This internship comes without any stipend", 0),
        ("Stipend: Nil", 0),
        ("Stipend: ₹30,000 – ₹50,000 per month", 50000),  # noqa: RUF001 - real JD en dash
        ("fixed stipend of Rs.10k/month", 10000),
        ("Stipend: 15K per month", 15000),  # no currency sign
        ("Stipend 9 500 per month", 9500),  # a space splits the thousands
        ("Stipend: ₹1,10,000 per month", 110000),  # Indian grouping, not its "10,000" tail
        ("Salary: ₹80,000 - ₹1,20,000 per month", 120000),
        ("compensation: $250pm", None),  # a dollar figure is not rupees
        ("Integrate Razorpay subscriptions for our ₹999/month plan", None),  # "pay" in a word
        ("Stipend ₹5,000 - ₹8,000 per month in training, then stipend ₹25,000 per month", 25000),
        ("Stipend: 0-20k based on performance", None),  # a range from 0 is not unpaid
        ("Pay: ₹1.00 - ₹2.00 per month. This role does not include a stipend.", 0),
        ("Pay: ₹15,000 /month", 15000),
        # an amount beats an unpaid line elsewhere
        ("Unpaid for the first week, then a stipend of ₹15,000 per month", 15000),
        # benefits and silence state no pay
        ("₹1,000 per month learning reimbursement", None),
        ("Unpaid leave as per policy", None),
        ("Competitive stipend", None),
        ("", None),
    ],
)
def test_stated_monthly_pay_inr(text: str, pay: int | None) -> None:
    assert signals.stated_monthly_pay_inr(text) == pay


@pytest.mark.parametrize(
    ("text", "unpaid"),
    [
        # real postings from the audit
        ("Internship Type: Unpaid", True),
        ("role - unpaid AI intern. Location - Bangalore", True),
        ("Internship: 3 months, unpaid, with a strong possibility of paid roles", True),
        ("Backend Developer Intern (Unpaid | NGO tech initiative)", True),
        ("Type: On-site | Regular | Unpaid", True),
        # not the pay of this role
        ("Up to 12 weeks of unpaid parental leave", False),
        ("Build services that chase unpaid invoices", False),
        ("We never offer unpaid internships", False),
    ],
)
def test_says_unpaid_reads_only_the_role_pay(text: str, unpaid: bool) -> None:
    assert signals.says_unpaid(text) is unpaid


def test_says_unpaid_ignores_benefit_lines_and_paid_roles() -> None:
    assert signals.says_unpaid("<p>Stipend: <b>Unpaid</b></p>")
    assert not signals.says_unpaid("No unpaid overtime, ever")
    assert not signals.says_unpaid("Unpaid for the first week, then a stipend of ₹15,000 per month")
    assert not signals.says_unpaid("Stipend not disclosed")


@pytest.mark.parametrize(
    ("text", "months"),
    [
        ("Duration: 6 months", 6.0),
        ("are available for duration of 4 months", 4.0),
        ("internship duration : 6months", 6.0),
        ("This is a paid 6-month internship", 6.0),
        ("a 26 weeks internship starting Jan 2027", 6.0),
        ("this 12-month apprenticeship", 12.0),
        ("Duration: 1 year", 12.0),
        ("internship for a period of three months", 3.0),
        ("a 5-6-month internship running from January", 5.0),  # a range counts by its lower bound
        ("2 month training program, then Duration: 6 months", 6.0),  # the longest statement
        ("Duration: 3 months. PPO hires join a 12-month training program", 3.0),  # own length wins
        ("Internship duration: 3 months. Probation duration: 6 months", 3.0),
        ("2 month training program", 2.0),  # a program length when no internship length
        ("In this 36 month internship", None),  # a "3-6" with its hyphen stripped, not 3 years
        ("Duration: 2 months. For students of a 4-year program", 2.0),
        # no duration stated
        ("6 months of experience with Python", None),
        ("PPO after 6 months based on performance", None),
        ("Posted 1 month ago", None),
        ("", None),
    ],
)
def test_internship_months(text: str, months: float | None) -> None:
    assert signals.internship_months(text) == months

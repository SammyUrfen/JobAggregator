"""Jobs routes: GET / (server-rendered filtered card grid) + detail modal + card actions.

Filters are GET query params (bookmarkable); only the whitelisted ORDER BY fragment and the
whitelisted action column are ever interpolated — every user value binds via `?`. The detail
modal body (GET /api/jobs/{uid}/detail) flattens any HTML description to plain text server-side
(html_to_text) so untrusted source markup never renders. Actions return the updated card partial.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.datastructures import QueryParams

from job_aggregator.dashboard.deps import (
    SchedulerProtocol,
    get_conn,
    get_scheduler,
    get_templates,
    header_context,
)
from job_aggregator.errors import NotFoundError

router = APIRouter()

PAGE_SIZE = 50
MAX_Q_LEN = 200
# Cap the detail-modal description. Source descriptions can be arbitrarily long (or hostile);
# the modal scrolls, but an unbounded blob is pointless — the original posting has the full text.
MAX_DESC_CHARS = 12000
# Closing one of these ends a line when flattening HTML to text. `<br>` is handled on open
# (it is void — no close), so it is deliberately NOT in this set.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "ul",
        "ol",
        "tr",
        "section",
        "article",
        "header",
        "footer",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)
# Their text content is code/markup, never prose — drop it entirely, don't just neutralize it.
_SKIP_TAGS = frozenset({"script", "style", "noscript"})
BUCKET_KEYS = ("pass", "unknown", "fail")
_STATUS_VALUES = ("new", "active", "stale", "deleted")
_SORT_OPTIONS = ("score", "date", "salary")
_TRUTHY = {"1", "true", "on", "yes"}

# Whitelisted ORDER BY per sort key (the only interpolated SQL besides an action column).
_ORDER_BY = {
    "score": "match_score DESC, posted_at DESC",
    "date": "posted_at IS NULL, posted_at DESC, match_score DESC",
    "salary": "salary_min IS NULL, salary_min DESC, match_score DESC",
}
# action -> (column, value). hide/unhide route through the `hidden` column.
_ACTIONS = {
    "apply": ("applied", 1),
    "unapply": ("applied", 0),
    "bookmark": ("bookmarked", 1),
    "unbookmark": ("bookmarked", 0),
    "hide": ("hidden", 1),
    "unhide": ("hidden", 0),
}


class _TextExtractor(HTMLParser):
    """Flatten HTML to plain text: keep text nodes, drop tags, insert a newline around block tags.
    `convert_charrefs=True` (default) means entities arrive already decoded in handle_data."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0  # depth inside a script/style/noscript subtree

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "br":  # void: no close tag to hang the newline on
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    """Render a (possibly HTML) source description as safe, readable plain text.

    We flatten to text server-side rather than sanitizing+rendering HTML: descriptions come from
    untrusted external sources, so stripping to text sidesteps stored XSS entirely (the template
    then auto-escapes the result). Collapses runs of blank lines and caps length.
    """
    if not raw:
        return ""
    parser = _TextExtractor()
    parser.feed(raw)
    # Drop blank lines entirely so output is deterministic regardless of the source's incidental
    # whitespace between tags; non-empty lines are joined by a single newline.
    lines = (line.strip() for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line).strip()[:MAX_DESC_CHARS]


@dataclass(frozen=True)
class JobQuery:
    q: str | None = None
    source: str | None = None
    remote: str | None = None  # "yes" | "no" | None
    bucket: str | None = None
    status: str | None = None  # a _STATUS_VALUES member, "all", or None (default view)
    show_hidden: bool = False
    applied: bool = False
    bookmarked: bool = False
    sort: str = "score"
    page: int = 1


class JobAction(BaseModel):
    action: Literal["apply", "unapply", "bookmark", "unbookmark", "hide", "unhide"]


def _parse_job_query(params: QueryParams) -> JobQuery:
    """Whitelist every field; anything unexpected falls back to a safe default."""

    def _clean(key: str) -> str | None:
        value = params.get(key)
        value = value.strip() if value else ""
        return value or None

    q = _clean("q")
    if q and len(q) > MAX_Q_LEN:
        q = q[:MAX_Q_LEN]
    remote = params.get("remote") if params.get("remote") in ("yes", "no") else None
    bucket = params.get("bucket") if params.get("bucket") in BUCKET_KEYS else None
    status_raw = params.get("status")
    status = status_raw if status_raw in (*_STATUS_VALUES, "all") else None
    sort_raw = params.get("sort", "")
    sort = sort_raw if sort_raw in _SORT_OPTIONS else "score"
    try:
        page = max(1, int(params.get("page", "1")))
    except ValueError:
        page = 1
    return JobQuery(
        q=q,
        source=_clean("source"),
        remote=remote,
        bucket=bucket,
        status=status,
        show_hidden=params.get("show_hidden") in _TRUTHY,
        applied=params.get("applied") in _TRUTHY,
        bookmarked=params.get("bookmarked") in _TRUTHY,
        sort=sort,
        page=page,
    )


def _build_where(query: JobQuery) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query.q:
        clauses.append("(title LIKE ? OR company LIKE ?)")
        params += [f"%{query.q}%", f"%{query.q}%"]
    if query.source:
        clauses.append("source = ?")
        params.append(query.source)
    if query.remote == "yes":
        clauses.append("is_remote = 1")
    elif query.remote == "no":
        clauses.append("(is_remote = 0 OR is_remote IS NULL)")
    if query.bucket:
        clauses.append("salary_bucket = ?")
        params.append(query.bucket)
    if query.status is None:
        clauses.append("status != 'deleted'")  # default view hides soft-deleted
    elif query.status != "all":
        clauses.append("status = ?")
        params.append(query.status)
    if not query.show_hidden:
        clauses.append("hidden = 0")
    if query.applied:
        clauses.append("applied = 1")
    if query.bookmarked:
        clauses.append("bookmarked = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _query_jobs(conn: sqlite3.Connection, query: JobQuery) -> tuple[list[sqlite3.Row], int]:
    where, params = _build_where(query)
    total_row = conn.execute(f"SELECT COUNT(*) AS n FROM jobs {where}", params).fetchone()
    total = int(total_row["n"]) if total_row is not None else 0
    order_by = _ORDER_BY[query.sort]
    offset = (query.page - 1) * PAGE_SIZE
    rows: list[sqlite3.Row] = conn.execute(
        f"SELECT * FROM jobs {where} ORDER BY {order_by} LIMIT ? OFFSET ?",
        [*params, PAGE_SIZE, offset],
    ).fetchall()
    return rows, total


def _page_qs(params: QueryParams, page: int) -> str:
    kept = [(k, v) for k, v in params.multi_items() if k != "page"]
    kept.append(("page", str(page)))
    return urlencode(kept)


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    scheduler: SchedulerProtocol = Depends(get_scheduler),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    query = _parse_job_query(request.query_params)
    rows, total = _query_jobs(conn, query)
    sources = [
        r["source"] for r in conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source")
    ]
    context: dict[str, Any] = {
        **header_context(conn, scheduler),
        "jobs": rows,
        "total": total,
        "query": query,
        "sources": sources,
        "buckets": BUCKET_KEYS,
        "page": query.page,
        "page_size": PAGE_SIZE,
        "prev_qs": _page_qs(request.query_params, query.page - 1),
        "next_qs": _page_qs(request.query_params, query.page + 1),
    }
    return templates.TemplateResponse(request, "jobs.html", context)


@router.get("/api/jobs/{uid}/detail", response_class=HTMLResponse)
def job_detail(
    uid: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    """The detail-modal body for one job: facts + a flattened description + the original link."""
    row = conn.execute("SELECT * FROM jobs WHERE job_uid = ?", (uid,)).fetchone()
    if row is None:
        raise NotFoundError("job not found", details={"uid": uid})
    context = {"job": row, "description_text": html_to_text(row["description"])}
    return templates.TemplateResponse(request, "partials/job_detail.html", context)


@router.post("/api/jobs/{uid}/action", response_class=HTMLResponse)
def job_action(
    uid: str,
    body: JobAction,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    column, value = _ACTIONS[body.action]  # both whitelisted -> safe to interpolate column
    cur = conn.execute(f"UPDATE jobs SET {column} = ? WHERE job_uid = ?", (value, uid))
    conn.commit()
    if cur.rowcount == 0:
        raise NotFoundError("job not found", details={"uid": uid})
    row = conn.execute("SELECT * FROM jobs WHERE job_uid = ?", (uid,)).fetchone()
    return templates.TemplateResponse(request, "partials/job_card.html", {"job": row})

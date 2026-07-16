"""Render a TailoredResume into the packaged LaTeX template, then optionally compile to PDF.

The template's preamble (macros) is kept verbatim; only the document BODY is regenerated from the
tailored data using the template's own macros (\\resumeProjectHeading, \\techstack, \\resumeItem…).
All dynamic text is LaTeX-escaped, so a stray & or % in a company name can't break compilation.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from job_aggregator.errors import RenderError
from job_aggregator.paths import default_resume_template

if TYPE_CHECKING:
    from job_aggregator.profile.schema import Contact, Education, Profile
    from job_aggregator.resume.tailor import TailoredResume

_DOC_START = r"\begin{document}"
# LaTeX engines we can shell out to, in preference order (tectonic is self-contained).
_ENGINES = ("tectonic", "pdflatex")
_PDF_TIMEOUT_S = 120.0

# Order matters: backslash must be escaped first, so we iterate this list, not a dict.
_LATEX_REPLACEMENTS = (
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
)


def _esc(text: str) -> str:
    for target, repl in _LATEX_REPLACEMENTS:
        text = text.replace(target, repl)
    return text


def _bullets(items: list[str]) -> str:
    inner = "\n".join(f"      \\resumeItem{{{_esc(b)}}}" for b in items)
    return f"    \\resumeItemListStart\n{inner}\n    \\resumeItemListEnd"


def _header(contact: Contact) -> str:
    fields: list[str] = []
    if contact.phone:
        fields.append(_esc(contact.phone))
    fields.append(f"\\hrefuline{{mailto:{_esc(contact.email)}}}{{{_esc(contact.email)}}}")
    fields += [f"\\hrefuline{{{_esc(link.url)}}}{{{_esc(link.label)}}}" for link in contact.links]
    joined = " $|$\n    ".join(fields)
    return (
        "\\begin{center}\n"
        f"    {{\\large \\scshape {_esc(contact.name)}}} \\\\ \\vspace{{3pt}}\n"
        f"    \\small\n    {joined}\n"
        "\\end{center}"
    )


def _projects(tailored: TailoredResume) -> str:
    blocks: list[str] = []
    for p in tailored.projects:
        head = _esc(p.name)
        if p.tagline:
            head += f" \\textmd{{\\textnormal{{--- {_esc(p.tagline)}}}}}"
        heading = f"  \\resumeProjectHeading\n    {{\\hrefuline{{{_esc(p.url or '')}}}{{{head}}}}}"
        tech = f"  \\techstack{{{_esc(', '.join(p.tech))}}}" if p.tech else ""
        parts = [heading]
        if tech:
            parts.append(tech)
        parts.append(_bullets(p.bullets))
        blocks.append("\n".join(parts))
    body = "\n\n".join(blocks)
    return (
        f"\\section{{Projects}}\n\\resumeSubHeadingListStart\n\n{body}\n\n\\resumeSubHeadingListEnd"
    )


def _education(items: list[Education]) -> str:
    if not items:
        return ""
    blocks: list[str] = []
    for e in items:
        dates = _esc(f"{e.start or ''} -- {e.end or ''}".strip(" -"))
        name = _esc(e.institution)
        if e.url:
            name = f"\\hrefplain{{{_esc(e.url)}}}{{{name}}}"
        grade = f"\n{_bullets([e.grade])}" if e.grade else ""
        blocks.append(
            f"  \\resumeEduHeading\n    {{{name}}}{{{dates}}}\n"
            f"    {{{_esc(e.degree)}}}{{{_esc(e.location or '')}}}{grade}"
        )
    return (
        "\\section{Education}\n\\resumeSubHeadingListStart\n\n"
        + "\n\n".join(blocks)
        + "\n\n\\resumeSubHeadingListEnd"
    )


def _skills(tailored: TailoredResume) -> str:
    if not tailored.skills:
        return ""
    rows = " \\\\[2pt]\n  ".join(
        f"\\textbf{{{_esc(g.category)}}} & {_esc(', '.join(g.items))}" for g in tailored.skills
    )
    return (
        "\\section{Technical Skills}\n"
        "\\begin{itemize}[leftmargin=0.0in, label={}]\n\\small{\\item{\n"
        "\\begin{tabularx}{\\linewidth}{@{}l@{\\hspace{1.5em}}X@{}}\n  "
        f"{rows} \\\\\n"
        "\\end{tabularx}\n}}\n\\end{itemize}"
    )


def _achievements(items: list[str]) -> str:
    if not items:
        return ""
    return (
        "\\section{Achievements}\n\\resumeSubHeadingListStart\n  \\item\n"
        f"{_bullets(items)}\n\\resumeSubHeadingListEnd"
    )


def render_latex(
    profile: Profile, tailored: TailoredResume, *, template_path: Path | None = None
) -> str:
    """Fill the template's macros with the tailored content. Returns a complete .tex document."""
    template = (template_path or default_resume_template()).read_text(encoding="utf-8")
    if _DOC_START not in template:
        raise RenderError(
            "template has no \\begin{document}", details={"template": str(template_path)}
        )
    preamble = template.split(_DOC_START, 1)[0]
    sections = [
        _header(profile.contact),
        _projects(tailored),
        _education(profile.education),
        _skills(tailored),
        _achievements(profile.achievements),
    ]
    body = "\n\n".join(s for s in sections if s)
    return f"{preamble}{_DOC_START}\n\n{body}\n\n\\end{{document}}\n"


def _find_engine() -> str | None:
    return next((e for e in _ENGINES if shutil.which(e)), None)


def compile_pdf(tex: str, out_pdf: Path, *, engine: str | None = None) -> Path:
    """Compile a .tex string to `out_pdf`. Raises RenderError if no engine is installed or the
    build fails. Kept behind this seam so the rest of the pipeline is testable without LaTeX."""
    chosen = engine or _find_engine()
    if chosen is None:
        raise RenderError(
            "no LaTeX engine found — install tectonic or a TeX distribution (pdflatex)",
            details={"tried": list(_ENGINES)},
        )
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "resume.tex").write_text(tex, encoding="utf-8")
        cmd = (
            [chosen, "resume.tex"]
            if chosen == "tectonic"
            else [chosen, "-interaction=nonstopmode", "-halt-on-error", "resume.tex"]
        )
        proc = subprocess.run(
            cmd, cwd=work, capture_output=True, text=True, timeout=_PDF_TIMEOUT_S, check=False
        )
        produced = work / "resume.pdf"
        if proc.returncode != 0 or not produced.exists():
            raise RenderError(
                f"{chosen} failed to build the PDF",
                details={"stderr": proc.stderr[-400:] or proc.stdout[-400:]},
            )
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, out_pdf)
    return out_pdf

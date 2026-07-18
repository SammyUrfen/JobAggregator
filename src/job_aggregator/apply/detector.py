"""Set-of-Marks form-field detection (Track D), adapted from the user's Form Controller Agent
(`Gen_AI/Form Controller Agent/browser/element_detector.py`).

A JS snippet enumerates the page's visible, enabled form fields and returns each one's label, type,
current value, and click coordinates (center, from getBoundingClientRect). CODE owns geometry; the
LLM only maps applicant values onto these fields (see grounding.py) — it never guesses pixels. The
`detect_fields` call is browser-bound (`# pragma: no cover`); the `Field` model is plain data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Enumerate fillable/interactive fields, derive a best-effort label (label[for] / wrapping <label> /
# aria / shadcn-react-hook-form `data-slot=form-item` / placeholder / name), skip hidden/disabled/
# offscreen, and return center coordinates + current value (adapted from element_detector.js).
_DETECT_JS = r"""
() => {
  const SELECTORS = ["input:not([type=hidden])", "textarea", "select", "[contenteditable=true]"];
  function labelFor(el) {
    if (el.id) {
      const l = document.querySelector('label[for="' + el.id + '"]');
      if (l) return l.innerText.trim();
    }
    const wrap = el.closest('label'); if (wrap) return wrap.innerText.trim();
    const al = el.getAttribute('aria-label'); if (al) return al.trim();
    const ab = el.getAttribute('aria-labelledby');
    if (ab) { const r = document.getElementById(ab); if (r) return r.innerText.trim(); }
    const fi = el.closest('[data-slot="form-item"]');
    if (fi) {
      const ll = fi.querySelector('label,[data-slot="form-label"]');
      if (ll) return ll.innerText.trim();
    }
    if (el.placeholder) return el.placeholder.trim();
    return (el.getAttribute('name') || '').trim();
  }
  const out = [];
  for (const sel of SELECTORS) {
    for (const el of document.querySelectorAll(sel)) {
      const s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden') continue;
      if (parseFloat(s.opacity) === 0) continue;
      if (el.disabled || el.readOnly) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
      const type = (el.getAttribute('type') || el.tagName).toLowerCase();
      out.push({
        tag: el.tagName.toLowerCase(), type,
        name: el.getAttribute('name') || '', label: labelFor(el),
        x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
        value: (el.value || el.innerText || '').slice(0, 80), isFile: type === 'file',
      });
    }
  }
  return out;
}
"""


@dataclass
class Field:
    index: int
    tag: str
    type: str
    name: str
    label: str
    x: int
    y: int
    value: str
    is_file: bool

    @property
    def filled(self) -> bool:
        return bool(self.value.strip())

    @property
    def is_text(self) -> bool:
        """A free-text field the LLM can type into (not a file input / native <select>)."""
        return not self.is_file and self.tag != "select" and self.type != "select"

    def describe(self) -> str:
        state = f"value={self.value!r}" if self.filled else "EMPTY"
        return f"[{self.index}] {self.label or self.name or self.type} ({self.type}) {state}"


def detect_fields(page: Any) -> list[Field]:  # pragma: no cover - needs a real browser
    rows = page.evaluate(_DETECT_JS)
    return [
        Field(
            index=i,
            tag=r["tag"],
            type=r["type"],
            name=r["name"],
            label=r["label"],
            x=int(r["x"]),
            y=int(r["y"]),
            value=r["value"],
            is_file=bool(r["isFile"]),
        )
        for i, r in enumerate(rows)
    ]

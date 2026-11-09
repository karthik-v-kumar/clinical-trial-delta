"""Parse a registry eligibility-criteria block into a structured checklist.

ClinicalTrials.gov stores eligibility as one free-text field. Most records
follow the same shape:

    Inclusion Criteria:

    * Adults aged 18 or older
    * BMI of 30 or more

    Exclusion Criteria:

    * Type 1 diabetes

This module turns that into {"inclusion": [...], "exclusion": [...]} items so the
trial page can show a checklist. It is deterministic (no LLM) and conservative:
it never rewrites or drops criteria text, and when it cannot tell inclusion from
exclusion it says so (parsed = False) and the page shows the original text.

Checklist shape (see writers.CHECKLIST_FIELDS / CHECKLIST_ITEM_FIELDS):

    {
      "parsed": true,
      "inclusion": [{"text": "...", "group": "Main phase", "sub_items": ["..."]}],
      "exclusion": [{"text": "..."}],
      "notes": ["text that sits outside both sections"]
    }

"group" and "sub_items" appear only when the registry text has them.
"""

from __future__ import annotations

import re

# Backslash escapes the registry adds to keep its Markdown literal ("\\>= 18").
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>~|^&])")

_QUALIFIERS = r"(?:key|main|major|general|principal|primary|core|additional|study|sub-?study|other)"
# A section heading: optional bullet/markup, optional qualifiers, "inclusion" or
# "exclusion" (or "non-inclusion", which means exclusion), then "criteria".
_HEADING = re.compile(
    rf"""^[\s*\-•#_\d.)(<\[]*                    # bullet, numbering, bold or bracket markers
        (?:{_QUALIFIERS}\s+)*
        (?P<non>non[-\s]?)?
        (?P<kind>inclusion|exclusion)s?
        (?P<criteria>[\s_]+criteri(?:a|on))?
        (?P<rest>.*)$""",
    re.IGNORECASE | re.VERBOSE,
)
_BULLET = re.compile(
    r"""^(?P<indent>\s*)
        (?P<marker>[*\-•·▪◦‣➢]|\d{1,2}[.)]|\(\d{1,2}\)|[a-zA-Z][.)]|\([a-zA-Z]\)|[ivxIVX]{2,4}[.)])
        \s+(?P<text>\S.*)$""",
    re.VERBOSE,
)
_WS = re.compile(r"\s+")
_LETTER_MARKER = re.compile(r"^(?:[a-zA-Z][.)]|\([a-zA-Z]\)|[ivxIVX]{2,4}[.)])$")
MAX_GROUP_LENGTH = 100


def unescape(text: str) -> str:
    return _ESCAPE.sub(r"\1", text)


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _heading(line: str, next_is_bullet: bool = False) -> tuple[str, str | None, str | None] | None:
    """(kind, group, inline_text) if the line is a section heading, else None."""
    m = _HEADING.match(line)
    if not m:
        return None
    rest = m.group("rest").strip().strip("*_>]").strip()
    has_criteria = bool(m.group("criteria"))
    kind = m.group("kind").lower()
    if m.group("non"):
        kind = "exclusion"

    if rest and rest[0] in ":：":
        inline = rest[1:].strip()
        return (kind, None, inline or None)
    if _BULLET.match(line):
        # A list item is only a heading when a colon follows ("* Inclusion Criteria:").
        # "6. Other exclusion criteria" and "* Other exclusion criteria may apply" are items.
        return None
    if not rest:
        # "Inclusion Criteria" / "Exclusion" alone on a line.
        return (kind, None, None)
    if rest[0] in "-–—" and has_criteria:
        inline = rest[1:].strip()
        return (kind, None, inline or None)
    if has_criteria and rest[-1] in ":：" and len(rest) <= MAX_GROUP_LENGTH:
        # "Inclusion Criteria for healthy subjects:" -> a labelled group.
        return (kind, _clean(rest[:-1]), None)
    if has_criteria and rest.startswith("(") and rest.endswith(")") and len(rest) <= MAX_GROUP_LENGTH:
        return (kind, _clean(rest[1:-1]), None)
    if has_criteria and next_is_bullet and len(rest) <= MAX_GROUP_LENGTH:
        # "Inclusion criteria for HNF1A-patients" (no colon) directly above a list.
        return (kind, _clean(rest), None)
    if has_criteria and (rest[0].isupper() or rest[0].isdigit()):
        # "Inclusion criteria Patients between 18 and 80 ...": heading and text on one line.
        return (kind, None, rest)
    # "Other protocol-defined exclusion criteria may apply": an ordinary sentence.
    return None


def parse_criteria(text: str | None) -> dict:
    """Parse a criteria block. Always returns a checklist dict (see module docstring)."""
    empty = {"parsed": False, "inclusion": [], "exclusion": [], "notes": []}
    if not text or not text.strip():
        return empty

    sections: dict[str, list[dict]] = {"inclusion": [], "exclusion": []}
    notes: list[str] = []
    section: str | None = None
    group: str | None = None
    current: dict | None = None  # the item being built
    base_indent: int | None = None
    top_marker_is_letter = False
    blank_before = True
    saw_heading = False

    def start_item(body: str) -> None:
        nonlocal current
        current = {"text": _clean(body)}
        if group:
            current["group"] = group
        sections[section].append(current)

    lines = unescape(text).replace("\r\n", "\n").split("\n")
    for index, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip():
            blank_before = True
            continue

        next_is_bullet = _next_content_is_bullet(lines, index)
        heading = _heading(line, next_is_bullet)
        if heading:
            kind, heading_group, inline = heading
            saw_heading = True
            section, group, current, base_indent = kind, heading_group, None, None
            if inline:
                start_item(inline)
            blank_before = False
            continue

        bullet = _BULLET.match(line)
        if section is None:
            # Text before the first heading belongs to neither list.
            notes.append(_clean(bullet.group("text") if bullet else line))
            blank_before = False
            continue

        if bullet:
            indent = len(bullet.group("indent"))
            is_letter = bool(_LETTER_MARKER.match(bullet.group("marker")))
            if base_indent is None:
                base_indent, top_marker_is_letter = indent, is_letter
            nested = current is not None and (
                indent > base_indent or (is_letter and not top_marker_is_letter and indent == base_indent)
            )
            if nested:
                current.setdefault("sub_items", []).append(_clean(bullet.group("text")))
            else:
                start_item(bullet.group("text"))
            blank_before = False
            continue

        stripped = line.strip()
        if stripped.endswith((":", "：")) and len(stripped) <= MAX_GROUP_LENGTH and next_is_bullet and blank_before:
            # "Main phase:" on its own line, followed by bullets: a group label.
            group, current, base_indent = _clean(stripped[:-1]), None, None
        elif current is not None and not blank_before:
            # A wrapped line: continue the item (or its last sub-item).
            if current.get("sub_items"):
                current["sub_items"][-1] = _clean(current["sub_items"][-1] + " " + stripped)
            else:
                current["text"] = _clean(current["text"] + " " + stripped)
        else:
            # A criterion written as a plain paragraph.
            start_item(stripped)
        blank_before = False

    inclusion, exclusion = sections["inclusion"], sections["exclusion"]
    parsed = saw_heading and bool(inclusion or exclusion)
    if not parsed:
        return empty
    return {"parsed": True, "inclusion": inclusion, "exclusion": exclusion, "notes": notes}


def _next_content_is_bullet(lines: list[str], index: int) -> bool:
    for later in lines[index + 1 :]:
        if later.strip():
            return bool(_BULLET.match(later)) and _heading(later) is None
    return False


def checklist_text(checklist: dict) -> str:
    """All criterion text in a checklist, joined (used to check nothing was lost)."""
    parts: list[str] = list(checklist.get("notes", []))
    for key in ("inclusion", "exclusion"):
        for item in checklist.get(key, []):
            if item.get("group"):
                parts.append(item["group"])
            parts.append(item["text"])
            parts.extend(item.get("sub_items", []))
    return " ".join(parts)

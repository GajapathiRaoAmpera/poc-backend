"""Mockup rules (spec §4): static, self-contained HTML; every MUST requirement is on a screen."""

import re
from html.parser import HTMLParser

from app.modules.agents.design.schema import MAX_HTML, MAX_SCREENS, DesignAgentOutput, ScreenChange
from app.modules.artifacts.models import ValidationIssue

_TAGS = set(
    "html head body title style main header footer nav section article aside div span p "
    "h1 h2 h3 h4 h5 h6 a ul ol li dl dt dd strong em b i u small br hr img figure figcaption "
    "table thead tbody tfoot tr th td caption form label input textarea select option button "
    "fieldset legend blockquote code pre time mark abbr progress meter details summary picture "
    "source".split()
)
_ATTRS = set(
    "class id style href src alt title type name value placeholder for role width height colspan "
    "rowspan checked disabled selected readonly required min max step rows cols lang dir tabindex "
    "open".split()
)
_IMG = r"data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]*"
_SRC = re.compile(_IMG, re.I)
_CSS_BAD = re.compile(r"\\|@import|expression\(|image-set|-moz-binding|behavior\s*:", re.I)
_CSS_URL = re.compile(r"url\(\s*(['\"]?)\s*([^'\")]*)", re.I)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_DOCTYPE = re.compile(r"\s*<!doctype html>", re.I)
_SCRIPT_URL = re.compile(r"(java|vb)script:")


def _err(code: str, message: str, path: str | None = None) -> ValidationIssue:
    return ValidationIssue(severity="error", code=code, message=message, path=path)


def _css_problem(css: str) -> str | None:
    css = _CSS_COMMENT.sub(" ", css)
    if _CSS_BAD.search(css):
        return "CSS uses a backslash, @import, expression, image-set, -moz-binding or behavior"
    for _, target in _CSS_URL.findall(css):
        if not (target.startswith("#") or _SRC.fullmatch(target.strip())):
            return "CSS url() must point to #fragment or an inline data:image"
    return None


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []
        self._style = False

    def handle_starttag(self, tag, attrs):
        if tag not in _TAGS:
            self.problems.append(f"<{tag}> is not allowed")
        self._style = tag == "style"
        for name, value in attrs:
            v = (value or "").strip()
            if name == "style":
                if p := _css_problem(v):
                    self.problems.append(p)
            elif name == "href":
                if not v.startswith("#"):
                    self.problems.append("href must be #anchor")
            elif name == "src":
                if not _SRC.fullmatch(v):
                    self.problems.append("src must be an inline data:image/png|jpeg|gif|webp")
            elif not (name in _ATTRS or name.startswith(("aria-", "data-"))):
                self.problems.append(f"attribute {name} is not allowed")

    def handle_endtag(self, tag):
        if tag == "style":
            self._style = False

    def handle_data(self, data):
        if self._style and (p := _css_problem(data)):
            self.problems.append(p)


def html_issues(html: str, path: str | None = None) -> list[ValidationIssue]:
    problems: list[str] = []
    rest = _DOCTYPE.sub("", html, count=1)
    low = rest.lower()
    squashed = "".join(ch for ch in low if not ch.isspace() and ord(ch) > 31)
    if "<!" in rest or "<?" in rest:
        problems.append("comments, CDATA and processing instructions are not allowed")
    if "<script" in low or "</script" in low:
        problems.append("<script> is not allowed")
    if _SCRIPT_URL.search(squashed):
        problems.append("javascript: and vbscript: are not allowed")
    scan = _Scan()
    scan.feed(rest)
    scan.close()
    return [
        _err("HTML_UNSAFE", f"{path or 'html'}: {p}", path)
        for p in dict.fromkeys(problems + scan.problems)
    ]


def _reqs(requirements: dict) -> tuple[set[str], set[str]]:
    """(all ids, MUST functional ids) from the requirements content or an {id: priority} map."""
    if "requirements" in requirements:
        rows = {r["id"]: r for r in requirements["requirements"]}
        return set(rows), {
            i for i, r in rows.items() if r.get("priority") == "MUST" and i.startswith("FR-")
        }
    return set(requirements), {
        i for i, p in requirements.items() if p == "MUST" and i.startswith("FR-")
    }


def design_issues(out: DesignAgentOutput, requirements: dict) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    known, must = _reqs(requirements)
    if len(out.screens) > MAX_SCREENS:
        issues.append(_err("TOO_LARGE", f"At most {MAX_SCREENS} screens"))
    ids = {s.id for s in out.screens}
    covered: set[str] = set()
    for s in out.screens:
        if len(s.html.encode()) > MAX_HTML:
            issues.append(_err("TOO_LARGE", f"{s.id}: html is over {MAX_HTML // 1024} KB", s.id))
        issues += html_issues(s.html, s.id)
        for rid in s.requirement_ids:
            if rid not in known:
                issues.append(_err("REQ_UNKNOWN", f"{s.id}: unknown requirement {rid}", s.id))
        covered.update(s.requirement_ids)
    for f in out.flows:
        for sid in (f.from_screen, f.to_screen):
            if sid not in ids:
                issues.append(_err("FLOW_UNKNOWN", f"Flow '{f.action}' names unknown screen {sid}"))
    if missing := sorted(must - covered):
        issues.append(
            ValidationIssue(
                severity="error",
                code="MUST_UNCOVERED",
                message=f"MUST requirements not on any screen: {', '.join(missing)}",
                refs=missing,
            )
        )
    return issues


def apply_changes(out: DesignAgentOutput, changes: list[ScreenChange]) -> DesignAgentOutput:
    by_id = {s.id: s for s in out.screens}
    flows = list(out.flows)
    for c in changes:
        if c.op == "write":
            if c.screen is None:
                raise ValueError("Write needs a screen")
            by_id[c.screen.id] = c.screen
        else:
            sid = c.id or (c.screen.id if c.screen else None)
            if sid not in by_id:
                raise ValueError(f"Cannot delete {sid}: no such screen")
            del by_id[sid]
            flows = [f for f in flows if sid not in (f.from_screen, f.to_screen)]
    if not by_id:
        raise ValueError("Keep at least one screen")
    if len(by_id) > MAX_SCREENS:
        raise ValueError(f"At most {MAX_SCREENS} screens")
    return out.model_copy(
        update={"screens": sorted(by_id.values(), key=lambda s: s.id), "flows": flows}
    )

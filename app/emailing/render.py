"""Rendering of e-mail templates: ``{{var}}`` (escaped) and ``{{{var}}}`` (raw HTML) substitution."""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import Any, Mapping

_VAR_RE = re.compile(r"\{\{\{\s*([A-Za-z0-9_]+)\s*\}\}\}|\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_BODY_RE = re.compile(r"<body[^>]*>", re.I)


@dataclass(frozen=True)
class Rendered:
    subject: str
    preheader: str
    html: str
    text: str


def _val(variables: Mapping[str, Any], name: str) -> str:
    value = variables.get(name)
    return "" if value is None else str(value)


def substitute(source: str, variables: Mapping[str, Any], *, escape: bool = True) -> str:
    """``{{x}}`` is escaped when ``escape`` is true, ``{{{x}}}`` is always raw. Unknown names become empty."""

    def repl(match: re.Match[str]) -> str:
        raw_name, name = match.group(1), match.group(2)
        if raw_name:
            return _val(variables, raw_name)
        value = _val(variables, name)
        return _html.escape(value, quote=True) if escape else value

    return _VAR_RE.sub(repl, source or "")


def _one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def html_to_text(source: str) -> str:
    text = re.sub(r"(?is)<(head|style|script)[^>]*>.*?</\1>", "", source or "")
    text = re.sub(r"(?is)<!--.*?-->", "", text)

    def link(match: re.Match[str]) -> str:
        href = _html.unescape(match.group(1)).strip()
        label = re.sub(r"(?s)<[^>]+>", "", match.group(2))
        label = _one_line(_html.unescape(label))
        if not href or href.startswith(("#", "mailto:")) or href == label:
            return label or href
        return f"{label}: {href}" if label else href

    text = re.sub(r"(?is)<a\b[^>]*?href=[\"']([^\"']*)[\"'][^>]*>(.*?)</a>", link, text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|h[1-6]|table|ul|ol)>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", " ", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = _html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip() + "\n"


def _with_preheader(html_source: str, preheader: str) -> str:
    if not preheader:
        return html_source
    span = (
        '<span style="display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;'
        f'max-height:0;overflow:hidden;mso-hide:all;">{_html.escape(preheader)}</span>'
    )
    match = _BODY_RE.search(html_source)
    if match:
        return html_source[: match.end()] + span + html_source[match.end():]
    return span + html_source


def render(
    template_html: str,
    subject: str,
    preheader: str,
    variables: Mapping[str, Any],
    *,
    text_body: str | None = None,
) -> Rendered:
    """Render subject (plain), preheader (plain) and body (HTML) with the same variables."""
    subject_out = _one_line(substitute(subject, variables, escape=False))
    pre_out = _one_line(substitute(preheader, variables, escape=False))
    vars_full = dict(variables)
    vars_full["preheader"] = pre_out
    has_marker = bool(re.search(r"\{\{\{?\s*preheader\s*\}?\}\}", template_html or ""))
    body = substitute(template_html, vars_full)
    if not has_marker:
        body = _with_preheader(body, pre_out)
    if text_body and text_body.strip():
        text = substitute(text_body, vars_full, escape=False).strip() + "\n"
    else:
        text = html_to_text(body)
        if pre_out and text.startswith(pre_out):
            text = text[len(pre_out):].lstrip()
    return Rendered(subject_out, pre_out, body, text)

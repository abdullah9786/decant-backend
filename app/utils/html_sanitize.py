"""Server-side HTML allowlist for admin blog import (`admin_html` mode)."""

from __future__ import annotations

import nh3

# Max stored HTML size (defense against huge Word paste).
MAX_ADMIN_HTML_BYTES = 800_000

ADMIN_BLOG_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "br",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "sub",
        "sup",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "code",
        "hr",
        "ul",
        "ol",
        "li",
        "a",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "figure",
        "figcaption",
        "div",
        "span",
    }
)

_EMPTY = frozenset()

# Explicit per-tag allowlist (no wildcard) — predictable with nh3.
_ADMIN_ATTRS: dict[str, frozenset[str]] = {t: _EMPTY for t in ADMIN_BLOG_TAGS}
_ADMIN_ATTRS["a"] = frozenset({"href", "title"})
_ADMIN_ATTRS["img"] = frozenset({"src", "alt", "width", "height", "loading", "decoding"})
_ADMIN_ATTRS["td"] = frozenset({"colspan", "rowspan"})
_ADMIN_ATTRS["th"] = frozenset({"colspan", "rowspan", "scope"})


def sanitize_admin_blog_html(html: str) -> str:
    """Allowlisted HTML for `admin_html` posts. Strips scripts, handlers, iframes, etc."""
    if not html or not isinstance(html, str):
        return ""
    if len(html) > MAX_ADMIN_HTML_BYTES:
        html = html[:MAX_ADMIN_HTML_BYTES]
    return nh3.clean(
        html,
        tags=ADMIN_BLOG_TAGS,
        attributes=_ADMIN_ATTRS,
        url_schemes=frozenset({"http", "https", "mailto"}),
        link_rel="noopener noreferrer",
    )

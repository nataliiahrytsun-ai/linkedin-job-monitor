"""Safe display preparation for externally sourced job descriptions."""

from __future__ import annotations

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe
from lxml import etree, html
from lxml.html import HtmlElement

ALLOWED_DESCRIPTION_TAGS = frozenset(
    {
        "b",
        "br",
        "em",
        "h2",
        "h3",
        "h4",
        "i",
        "li",
        "ol",
        "p",
        "strong",
        "u",
        "ul",
    }
)
DROP_WITH_CONTENT_TAGS = frozenset(
    {
        "embed",
        "form",
        "iframe",
        "math",
        "noscript",
        "object",
        "script",
        "style",
        "svg",
        "template",
    }
)


def _plain_text_html(description: str) -> SafeString:
    normalized = description.replace("\r\n", "\n").replace("\r", "\n")
    escaped = str(escape(normalized)).replace("\n", "<br>\n")
    return mark_safe(escaped)


def _tag_name(element: HtmlElement) -> str | None:
    if not isinstance(element.tag, str):
        return None
    return etree.QName(element).localname.casefold()


def _serialized_children(wrapper: HtmlElement) -> str:
    parts = [str(escape(wrapper.text or ""))]
    parts.extend(
        html.tostring(child, encoding="unicode", method="html") for child in wrapper
    )
    return "".join(parts)


def sanitize_job_description(description: str | None) -> SafeString:
    """Allow minimal formatting while removing external markup and attributes."""
    if not description:
        return mark_safe("")

    try:
        wrapper = html.fragment_fromstring(description, create_parent="div")
    except (etree.ParserError, ValueError):
        return _plain_text_html(description)

    elements = list(wrapper.iterdescendants())
    if not any(_tag_name(element) is not None for element in elements):
        return _plain_text_html(description)

    for element in elements:
        tag_name = _tag_name(element)
        if tag_name is None:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
        elif tag_name in DROP_WITH_CONTENT_TAGS:
            element.drop_tree()
        elif tag_name in ALLOWED_DESCRIPTION_TAGS:
            element.attrib.clear()
        else:
            element.drop_tag()

    return mark_safe(_serialized_children(wrapper))

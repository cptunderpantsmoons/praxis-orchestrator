# src/praxis/features/email_templates.py
"""HTML email rendering for PRAXIS outbound replies.

Renders the cleaned text body into a branded HTML template (Tech gradient
style). Body text is escaped before insertion; paragraph breaks become <br>.
"""
from __future__ import annotations

from pathlib import Path

import markupsafe
from jinja2 import Environment, FileSystemLoader, select_autoescape

from praxis.features.sender_style import SenderStyle

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _derive_greeting(sender_email: str, sender_style: SenderStyle | None) -> str:
    if sender_style and sender_style.greeting:
        return sender_style.greeting
    if sender_email and "@" in sender_email:
        local_part = sender_email.split("@", 1)[0]
        first_segment = local_part.split(".", 1)[0]
        name = first_segment.split("+", 1)[0]
        if name:
            return f"Hello {name.replace('_', ' ').title()},"
    return "Hello,"


def _text_to_html_paragraphs(text: str) -> str:
    """Escape user content, then convert paragraph breaks to <br><br>."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    escaped = markupsafe.escape(normalized)
    return str(escaped).replace("\n\n", "<br><br>").replace("\n", "<br>")


def render_email_html(
    body_text: str,
    *,
    subject: str = "",
    sender_email: str = "",
    sender_style: SenderStyle | None = None,
) -> str:
    """Render the cleaned text body into the Tech gradient HTML template."""
    style = sender_style or SenderStyle(
        tone="professional", signature="— PRAXIS", language="en"
    )
    template = _env.get_template("email_reply.html")
    return template.render(
        subject=subject,
        greeting=_derive_greeting(sender_email, sender_style),
        body_html=_text_to_html_paragraphs(body_text),
        signature=style.signature or "— PRAXIS",
        tone=style.tone or "professional",
    )

# tests/test_email_templates.py
from praxis.features.email_templates import render_email_html
from praxis.features.sender_style import SenderStyle

def test_render_contains_gradient_header():
    html = render_email_html("Hello world", subject="Test", sender_email="john@example.com")
    assert "linear-gradient(135deg, #6366f1" in html
    assert "#8b5cf6" in html

def test_render_escapes_user_content():
    html = render_email_html("<script>alert(1)</script>", subject="Test")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html

def test_render_derives_greeting_from_local_part():
    html = render_email_html("Body", subject="Test", sender_email="jane.doe@example.com")
    assert "Hello Jane" in html

def test_render_uses_sender_style_greeting_when_set():
    style = SenderStyle(tone="professional", signature="— PRAXIS", language="en", greeting="Hi there")
    html = render_email_html("Body", subject="Test", sender_email="jane@example.com", sender_style=style)
    assert "Hi there" in html

def test_render_falls_back_to_hello_when_no_sender():
    html = render_email_html("Body", subject="Test")
    assert "Hello" in html

def test_render_converts_paragraph_breaks_to_br():
    html = render_email_html("Para one\n\nPara two", subject="Test")
    assert "<br>" in html
    assert "Para one" in html
    assert "Para two" in html

def test_render_includes_signature():
    style = SenderStyle(tone="professional", signature="— PRAXIS", language="en")
    html = render_email_html("Body", subject="Test", sender_style=style)
    assert "— PRAXIS" in html

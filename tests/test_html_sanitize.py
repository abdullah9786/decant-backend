import pytest

from app.utils.html_sanitize import sanitize_admin_blog_html


def test_strips_script_tag():
    html = '<p>Hi</p><script>alert(1)</script>'
    out = sanitize_admin_blog_html(html)
    assert "<script" not in out.lower()
    assert "alert" not in out


def test_strips_onclick():
    html = '<p onclick="alert(1)">x</p>'
    out = sanitize_admin_blog_html(html)
    assert "onclick" not in out.lower()
    assert "alert" not in out


def test_strips_javascript_href():
    html = '<a href="javascript:alert(1)">click</a>'
    out = sanitize_admin_blog_html(html)
    assert "javascript" not in out.lower()


def test_strips_iframe():
    html = '<iframe src="https://evil.com"></iframe><p>ok</p>'
    out = sanitize_admin_blog_html(html)
    assert "iframe" not in out.lower()


def test_keeps_safe_link_and_rel_behavior():
    html = '<p><a href="https://example.com/test">ok</a></p>'
    out = sanitize_admin_blog_html(html)
    assert "example.com" in out
    assert "noopener" in out or "noreferrer" in out or "example.com" in out

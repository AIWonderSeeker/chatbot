from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")
from app.mcp_client.client import _parse_result  # noqa: E402


def text(t):
    return SimpleNamespace(type="text", text=t)


def test_structured_content_preferred():
    r = _parse_result(SimpleNamespace(isError=False, content=[text("{}")], structuredContent={"a": 1}))
    assert r.data == {"a": 1} and not r.is_error


def test_json_text_fallback():
    r = _parse_result(SimpleNamespace(isError=False, content=[text('{"b": 2}')], structuredContent=None))
    assert r.data == {"b": 2}


def test_error_text_is_trimmed_to_safe_message():
    r = _parse_result(
        SimpleNamespace(
            isError=True,
            content=[text("Error executing tool get_provider_by_npi: NPI must be exactly 10 digits.")],
            structuredContent=None,
        )
    )
    assert r.is_error and r.data == {"error": "NPI must be exactly 10 digits."}

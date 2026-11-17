"""Bedrock inference settings: temperature is optional because newer Claude models reject it."""

import pytest

from app.agent.bedrock_client import BedrockConverseClient
from app.core.config import Settings


def _client(temperature):
    return BedrockConverseClient(
        region="us-east-1",
        model_id="us.anthropic.claude-sonnet-5",
        max_tokens=256,
        temperature=temperature,
        connect_timeout_s=1,
        read_timeout_s=1,
        max_attempts=1,
        max_concurrency=1,
    )


def test_temperature_omitted_by_default():
    client = _client(None)
    try:
        assert client._inference == {"maxTokens": 256}
    finally:
        client.close()


def test_temperature_sent_when_configured():
    client = _client(0.2)
    try:
        assert client._inference == {"maxTokens": 256, "temperature": 0.2}
    finally:
        client.close()


@pytest.mark.parametrize("raw, expected", [("", None), ("  ", None), ("0", 0.0), ("0.7", 0.7)])
def test_settings_parse_temperature(monkeypatch, raw, expected):
    monkeypatch.setenv("BEDROCK_TEMPERATURE", raw)
    assert Settings(_env_file=None).bedrock_temperature == expected


def test_settings_default_is_unset(monkeypatch):
    monkeypatch.delenv("BEDROCK_TEMPERATURE", raising=False)
    assert Settings(_env_file=None).bedrock_temperature is None

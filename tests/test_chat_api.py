"""API tests: FastAPI app with fake Bedrock and in-process tools."""

import uuid

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.agent.fake_bedrock import FakeBedrockClient  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.main import build_services, create_app  # noqa: E402
from tests.support.fakes import InProcessToolExecutor  # noqa: E402


def make_client(**overrides):
    settings = Settings(_env_file=None, mock_bedrock=True, **overrides)
    services = build_services(settings, model=FakeBedrockClient(), executor=InProcessToolExecutor())
    return TestClient(create_app(settings=settings, services=services))


@pytest.fixture
def client():
    with make_client() as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_ready(client):
    body = client.get("/ready").json()
    assert body["status"] == "ready" and body["checks"]["tools"] == "ok"


def test_chat_full_cycle(client):
    res = client.post("/chat", json={"message": "Who is provider 1234567893?"})
    assert res.status_code == 200
    body = res.json()
    assert "1234567893" in body["answer"]
    uuid.UUID(body["conversation_id"])
    duration = body["tool_calls"][0]["duration_ms"]
    assert body["tool_calls"] == [
        {"name": "get_provider_by_npi", "status": "success", "duration_ms": duration}
    ]
    assert res.headers["x-request-id"] == body["request_id"]


def test_tool_calls_expose_no_arguments_or_rows(client):
    body = client.post("/chat", json={"message": "Claims summary for 1234567893 in 2025"}).json()
    for call in body["tool_calls"]:
        assert set(call) == {"name", "status", "duration_ms"}


def test_conversation_id_round_trip(client):
    first = client.post("/chat", json={"message": "Who is 1234567893?"}).json()
    second = client.post(
        "/chat", json={"message": "And 1003000019?", "conversation_id": first["conversation_id"]}
    ).json()
    assert second["conversation_id"] == first["conversation_id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 4001},
        {"message": "hi", "conversation_id": "not-a-uuid"},
        {"message": "hi", "extra": "field"},
        {},
    ],
)
def test_validation_errors_do_not_echo_input(client, payload):
    res = client.post("/chat", json=payload)
    assert res.status_code == 422
    body = res.json()
    assert body["error"]["code"] == "invalid_request"
    assert "not-a-uuid" not in res.text and "xxxx" not in res.text


def test_message_limit_from_settings():
    with make_client(max_message_chars=10) as c:
        res = c.post("/chat", json={"message": "this is longer than ten"})
        assert res.status_code == 422 and res.json()["error"]["code"] == "message_too_long"


def test_tools_down_returns_503():
    settings = Settings(_env_file=None, mock_bedrock=True)
    executor = InProcessToolExecutor()
    executor.list_fails = True
    services = build_services(settings, model=FakeBedrockClient(), executor=executor)
    with TestClient(create_app(settings=settings, services=services)) as c:
        res = c.post("/chat", json={"message": "Who is 1234567893?"})
        assert res.status_code == 503 and res.json()["error"]["code"] == "tools_unavailable"
        assert c.get("/ready").status_code == 503


def test_alb_auth_required_when_enabled():
    with make_client(auth_mode="alb_oidc", alb_arn="arn:aws:elasticloadbalancing:x") as c:
        res = c.post("/chat", json={"message": "hi"})
        assert res.status_code == 401 and res.json()["error"]["code"] == "unauthorized"


def test_ui_served(client):
    res = client.get("/")
    assert res.status_code == 200 and "Claims data assistant" in res.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_mode": "none"},
        {"conversation_store": "memory"},
        {"mock_bedrock": True},
    ],
)
def test_production_rejects_unsafe_config(overrides):
    base = dict(
        environment="production",
        auth_mode="alb_oidc",
        alb_arn="arn:x",
        conversation_store="dynamodb",
        dynamodb_table="t",
        mock_bedrock=False,
        bedrock_model_id="m",
        mcp_auth_token="secret",
    )
    base.update(overrides)
    with pytest.raises(ValueError):
        Settings(_env_file=None, **base)

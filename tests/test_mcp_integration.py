"""Real MCP client <-> real MCP server over HTTP (mock Snowflake).

Run with: pytest -m integration
"""

import asyncio
import os
import socket
import threading
import time

import pytest

pytestmark = pytest.mark.integration
pytest.importorskip("mcp")
uvicorn = pytest.importorskip("uvicorn")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    os.environ.update({"MOCK_SNOWFLAKE": "true", "MCP_AUTH_TOKEN": "test-token"})
    from mcp_server.server import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def test_list_and_call_tools(server_url):
    from app.mcp_client.client import McpToolExecutor

    async def scenario():
        executor = McpToolExecutor(server_url, "test-token", timeout_s=10)
        names = {t.name for t in await executor.list_tools()}
        assert {"get_provider_by_npi", "get_provider_claim_summary", "get_claim_counts_by_status"} <= names
        async with executor.session() as session:
            ok = await session.call_tool("get_provider_by_npi", {"npi": "1234567893"})
            bad = await session.call_tool("get_provider_by_npi", {"npi": "12345"})
        assert not ok.is_error and ok.data["found"] is True
        assert bad.is_error and "10 digits" in bad.data["error"]

    asyncio.run(scenario())


def test_rejects_missing_token(server_url):
    from app.mcp_client.client import McpToolExecutor

    async def scenario():
        with pytest.raises(Exception):
            await McpToolExecutor(server_url, "wrong", timeout_s=5).list_tools()

    asyncio.run(scenario())


def test_health_is_open(server_url):
    import httpx

    assert httpx.get(server_url.replace("/mcp", "/health")).status_code == 200

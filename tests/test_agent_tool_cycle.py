"""Agent loop tests with a scripted model and in-process tools (no network)."""

import asyncio

import pytest

from app.agent.bedrock_agent import MAX_ROUNDS_MESSAGE, AgentSettings, BedrockAgent
from app.agent.errors import ModelThrottledError, RequestBudgetExceededError, ToolsUnavailableError
from app.agent.fake_bedrock import FakeBedrockClient
from app.agent.tool_registry import ToolRegistry
from app.agent.types import ToolSpec
from app.storage.memory import InMemoryConversationStore
from tests.support.fakes import InProcessToolExecutor, ScriptedConverse, final, tool_use

ALLOW = frozenset({"get_provider_by_npi", "get_provider_claim_summary", "get_claim_counts_by_status"})


def make_agent(model, executor=None, store=None, allow=ALLOW, **settings):
    executor = executor or InProcessToolExecutor()
    store = store or InMemoryConversationStore()
    registry = ToolRegistry(executor, allow)
    agent = BedrockAgent(
        model=model,
        registry=registry,
        executor=executor,
        store=store,
        settings=AgentSettings(**settings),
        prompt_builder=lambda: "SYSTEM",
    )
    return agent, executor, store


def run(coro):
    return asyncio.run(coro)


def test_single_tool_round_trip():
    model = ScriptedConverse(
        tool_use("get_provider_by_npi", {"npi": "1234567893"}, text="Checking."),
        final("Dr. X is a pediatrician."),
    )
    agent, executor, _ = make_agent(model)
    result = run(agent.process("Who is 1234567893?", None, "u1", budget_s=10))

    assert result.answer == "Dr. X is a pediatrician."
    assert [(r.name, r.status) for r in result.tool_calls] == [("get_provider_by_npi", "success")]
    assert executor.calls == [("get_provider_by_npi", {"npi": "1234567893"})]
    # Second request must carry a toolResult paired to the toolUse id.
    second = model.requests[1]["messages"]
    tool_result = second[-1]["content"][0]["toolResult"]
    assert second[-1]["role"] == "user"
    assert tool_result["toolUseId"] == "t1" and tool_result["status"] == "success"
    assert tool_result["content"][0]["json"]["found"] is True
    # Tool specs come from discovery, filtered by allowlist.
    names = {t["toolSpec"]["name"] for t in model.requests[0]["tool_config"]["tools"]}
    assert names == ALLOW


def test_tool_not_in_allowlist_is_rejected_and_not_executed():
    model = ScriptedConverse(tool_use("drop_everything", {}), final("Sorry."))
    agent, executor, _ = make_agent(model)
    result = run(agent.process("hi", None, "u1", budget_s=10))
    assert executor.calls == []
    assert result.tool_calls[0].status == "rejected"
    tr = model.requests[1]["messages"][-1]["content"][0]["toolResult"]
    assert tr["status"] == "error"


def test_server_tool_outside_allowlist_is_not_offered():
    executor = InProcessToolExecutor(extra_specs=[ToolSpec("run_sql", "danger", {"type": "object"})])
    model = ScriptedConverse(tool_use("run_sql", {"sql": "select 1"}), final("No."))
    agent, executor, _ = make_agent(model, executor=executor)
    result = run(agent.process("hi", None, "u1", budget_s=10))
    offered = {t["toolSpec"]["name"] for t in model.requests[0]["tool_config"]["tools"]}
    assert "run_sql" not in offered
    assert executor.calls == [] and result.tool_calls[0].status == "rejected"


def test_malformed_arguments_return_error_result():
    model = ScriptedConverse(tool_use("get_provider_by_npi", "not-an-object"), final("Bad input."))
    agent, executor, _ = make_agent(model)
    result = run(agent.process("x", None, "u1", budget_s=10))
    assert executor.calls == [] and result.tool_calls[0].status == "error"


def test_invalid_tool_input_surfaces_safe_error():
    model = ScriptedConverse(tool_use("get_provider_by_npi", {"npi": "12345"}), final("Invalid NPI."))
    agent, _, _ = make_agent(model)
    result = run(agent.process("x", None, "u1", budget_s=10))
    tr = model.requests[1]["messages"][-1]["content"][0]["toolResult"]
    assert tr["status"] == "error" and "10 digits" in tr["content"][0]["json"]["error"]
    assert result.tool_calls[0].status == "error"


def test_tool_exception_is_contained():
    executor = InProcessToolExecutor()
    executor.fail_with = RuntimeError("connection reset with secret details")
    model = ScriptedConverse(tool_use("get_provider_by_npi", {"npi": "1234567893"}), final("Failed."))
    agent, _, _ = make_agent(model, executor=executor)
    result = run(agent.process("x", None, "u1", budget_s=10))
    payload = model.requests[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert "secret" not in str(payload)
    assert result.tool_calls[0].status == "error"


def test_max_tool_rounds_enforced():
    rounds = [tool_use("get_provider_by_npi", {"npi": "1234567893"}, tool_id=f"t{i}") for i in range(5)]
    model = ScriptedConverse(*rounds)
    agent, executor, _ = make_agent(model, max_tool_rounds=2)
    result = run(agent.process("x", None, "u1", budget_s=10))
    assert result.answer == MAX_ROUNDS_MESSAGE
    assert len(executor.calls) == 2


def test_parallel_tool_uses_share_one_session():
    response = tool_use("get_provider_by_npi", {"npi": "1234567893"}, tool_id="a")
    response["output"]["message"]["content"].append(
        {"toolUse": {"toolUseId": "b", "name": "get_provider_by_npi", "input": {"npi": "1003000019"}}}
    )
    model = ScriptedConverse(response, final("Two providers."))
    agent, executor, _ = make_agent(model)
    run(agent.process("x", None, "u1", budget_s=10))
    assert executor.sessions_opened == 1
    ids = [b["toolResult"]["toolUseId"] for b in model.requests[1]["messages"][-1]["content"]]
    assert ids == ["a", "b"]


def test_no_tool_session_opened_when_not_needed():
    agent, executor, _ = make_agent(ScriptedConverse(final("I can only answer data questions.")))
    run(agent.process("hello", None, "u1", budget_s=10))
    assert executor.sessions_opened == 0


def test_model_errors_propagate_as_typed_errors():
    agent, _, store = make_agent(ScriptedConverse(ModelThrottledError("ThrottlingException")))
    with pytest.raises(ModelThrottledError):
        run(agent.process("x", None, "u1", budget_s=10))


def test_request_budget_enforced():
    agent, _, _ = make_agent(ScriptedConverse(final("late"), delay_s=2.0), model_timeout_s=5.0)
    with pytest.raises(RequestBudgetExceededError):
        run(agent.process("x", None, "u1", budget_s=1.5))


def test_tools_unavailable_raises():
    executor = InProcessToolExecutor()
    executor.list_fails = True
    agent, _, _ = make_agent(ScriptedConverse(final("x")), executor=executor)
    with pytest.raises(ToolsUnavailableError):
        run(agent.process("x", None, "u1", budget_s=10))


def test_guardrail_and_max_tokens_stops():
    agent, _, _ = make_agent(ScriptedConverse(final("", stop="guardrail_intervened")))
    assert "can't help" in run(agent.process("x", None, "u1", budget_s=10)).answer
    agent, _, _ = make_agent(ScriptedConverse(final("partial", stop="max_tokens")))
    assert run(agent.process("x", None, "u1", budget_s=10)).answer.startswith("partial")


def test_history_persists_text_only_and_is_replayed():
    store = InMemoryConversationStore()
    model = ScriptedConverse(
        tool_use("get_provider_by_npi", {"npi": "1234567893"}),
        final("First answer."),
        final("Second answer."),
    )
    agent, _, _ = make_agent(model, store=store)
    first = run(agent.process("Q1", None, "u1", budget_s=10))
    run(agent.process("Q2", first.conversation_id, "u1", budget_s=10))
    replayed = model.requests[2]["messages"]
    assert [m["role"] for m in replayed] == ["user", "assistant", "user"]
    assert all("text" in b for m in replayed for b in m["content"])  # no toolUse/toolResult


def test_conversation_owned_by_other_user_starts_fresh():
    store = InMemoryConversationStore()
    model = ScriptedConverse(final("A"), final("B"))
    agent, _, _ = make_agent(model, store=store)
    first = run(agent.process("Q1", None, "alice", budget_s=10))
    second = run(agent.process("Q2", first.conversation_id, "mallory", budget_s=10))
    assert second.conversation_id != first.conversation_id
    assert len(model.requests[1]["messages"]) == 1  # Alice's history not leaked


def test_fake_bedrock_full_cycle_against_real_tools():
    agent, executor, _ = make_agent(FakeBedrockClient())
    result = run(
        agent.process(
            "Summarize claims for provider 1234567893 between 2025-01-01 and 2025-06-30",
            None,
            "u1",
            budget_s=10,
        )
    )
    assert result.answer.startswith("[Mock model]")
    assert "1234567893" in result.answer
    assert executor.calls[0][0] == "get_provider_claim_summary"
    assert executor.calls[0][1]["start_date"] == "2025-01-01"


def test_fake_bedrock_declines_invalid_npi_without_tools():
    agent, executor, _ = make_agent(FakeBedrockClient())
    result = run(agent.process("How many claims for provider 12345?", None, "u1", budget_s=10))
    assert executor.calls == [] and "10 digits" in result.answer

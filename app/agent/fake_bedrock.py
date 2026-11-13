"""Deterministic stand-in for Bedrock Converse (MOCK_BEDROCK=true).

It emits real Converse-shaped responses with toolUse blocks, so the agent loop,
MCP client, MCP server, and SQL all run for real. It is not a language model:
it routes on regexes and keywords and says so in every answer.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

_NPI = re.compile(r"\b\d{10}\b")
_SHORT_ID = re.compile(r"\b\d{4,9}\b")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_YEAR = re.compile(r"\b(20\d{2})\b")
PREFIX = "[Mock model] "
DEFAULT_RANGE = ("2025-01-01", "2025-12-31")  # mock data covers 2024-2025


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _response(content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
    }


class FakeBedrockClient:
    async def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_config: dict[str, Any] | None,
        timeout_s: float,
    ) -> dict[str, Any]:
        last = messages[-1]
        if any("toolResult" in b for b in last["content"]):
            return _response([{"text": PREFIX + self._summarize(messages)}], "end_turn")

        text = " ".join(b.get("text", "") for b in last["content"])
        available = {t["toolSpec"]["name"] for t in (tool_config or {}).get("tools", [])}
        calls = self._plan(text, available)
        if not calls:
            return _response([{"text": PREFIX + self._no_tool_reply(text)}], "end_turn")
        content: list[dict[str, Any]] = [{"text": "Looking that up."}]
        for name, args in calls:
            content.append(
                {"toolUse": {"toolUseId": f"tooluse_{uuid.uuid4().hex[:12]}", "name": name, "input": args}}
            )
        return _response(content, "tool_use")

    @staticmethod
    def _date_range(text: str) -> tuple[str, str]:
        dates = _DATE.findall(text)
        if len(dates) >= 2:
            return dates[0], dates[1]
        years = [y for y in _YEAR.findall(_DATE.sub("", text))]
        if years:
            return f"{years[0]}-01-01", f"{years[-1]}-12-31"
        return DEFAULT_RANGE

    def _plan(self, text: str, available: set[str]) -> list[tuple[str, dict[str, Any]]]:
        lower = text.lower()
        npis = list(dict.fromkeys(_NPI.findall(_DATE.sub("", text))))
        start, end = self._date_range(text)
        if re.search(r"\b(select|sql|drop|delete|insert)\b", lower):
            return []  # never route raw SQL requests to a tool
        wants_claims = any(w in lower for w in ("claim", "billed", "paid", "summar", "patient"))
        calls: list[tuple[str, dict[str, Any]]] = []
        if "status" in lower and "get_claim_counts_by_status" in available:
            calls.append(("get_claim_counts_by_status", {"start_date": start, "end_date": end}))
        elif npis and wants_claims and "get_provider_claim_summary" in available:
            calls += [
                ("get_provider_claim_summary", {"npi": n, "start_date": start, "end_date": end})
                for n in npis[:3]
            ]
        elif npis and "get_provider_by_npi" in available:
            calls += [("get_provider_by_npi", {"npi": n}) for n in npis[:3]]
        elif wants_claims and not _SHORT_ID.search(text) and "get_claim_counts_by_status" in available:
            calls.append(("get_claim_counts_by_status", {"start_date": start, "end_date": end}))
        return calls

    @staticmethod
    def _no_tool_reply(text: str) -> str:
        if re.search(r"\b(select|sql|drop|delete|insert)\b", text.lower()):
            return "I can't run SQL. I can only use the approved data lookups."
        if _SHORT_ID.search(text):
            return "That doesn't look like a valid NPI. NPIs are 10 digits, for example 1234567893."
        return (
            "I can look up a provider by NPI, summarize a provider's claims for a date range, "
            "or count claims by status for a date range."
        )

    @staticmethod
    def _summarize(messages: list[dict[str, Any]]) -> str:
        names: dict[str, str] = {}
        for m in messages:
            for b in m.get("content", []):
                if "toolUse" in b:
                    names[b["toolUse"]["toolUseId"]] = b["toolUse"]["name"]
        parts: list[str] = []
        for b in messages[-1]["content"]:
            tr = b.get("toolResult")
            if not tr:
                continue
            data = tr["content"][0].get("json", {}) if tr.get("content") else {}
            name = names.get(tr.get("toolUseId", ""), "")
            if tr.get("status") == "error":
                parts.append(f"I couldn't complete that lookup: {data.get('error', 'unknown error')}")
            elif name == "get_provider_by_npi":
                if data.get("found"):
                    p = data["provider"]
                    parts.append(
                        f"{p.get('provider_name')} (NPI {p.get('npi')}) is an "
                        f"{str(p.get('entity_type', '')).lower()} provider, specialty "
                        f"{p.get('specialty')}, located in {p.get('state')}."
                    )
                else:
                    parts.append(f"No provider was found with NPI {data.get('npi')}.")
            elif name == "get_provider_claim_summary":
                patients = data.get("distinct_patients")
                patient_txt = (
                    f"fewer than {str(patients).lstrip('<')}"
                    if data.get("patient_count_suppressed")
                    else str(patients)
                )
                parts.append(
                    f"Provider {data.get('npi')} had {data.get('claim_count', 0):,} claims from "
                    f"{data.get('start_date')} to {data.get('end_date')}, covering {patient_txt} "
                    f"distinct patients. Billed {_money(data.get('total_billed_amount'))}, "
                    f"paid {_money(data.get('total_paid_amount'))}."
                )
            elif name == "get_claim_counts_by_status":
                rows = data.get("statuses", [])
                if not rows:
                    parts.append("No claims were found in that date range.")
                else:
                    lines = [
                        f"{r.get('claim_status')}: {r.get('claim_count'):,} claims, "
                        f"{_money(r.get('total_paid_amount'))} paid"
                        for r in rows
                    ]
                    parts.append(
                        f"Claims from {data.get('start_date')} to {data.get('end_date')} "
                        f"({data.get('total_claims', 0):,} total):\n" + "\n".join(lines)
                    )
            else:
                parts.append("I received data I don't know how to summarize.")
        return "\n\n".join(parts) or "No results were returned."

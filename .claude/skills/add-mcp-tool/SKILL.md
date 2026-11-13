---
name: add-mcp-tool
description: Add a new question type to the assistant as a new MCP tool, end to end — SQL template, validation, tool function, server registration, allowlist, fake-model routing, tests, and eval questions. Use when the user wants the chatbot to answer a new kind of question.
---

# Add an MCP tool

A new question type is a new tool. The model never writes SQL, so every capability is a
reviewed, parameterized template. Read CLAUDE.md "MCP tools and Snowflake" first.

## 1. Design (confirm with the user before coding)
- Tool name (`verb_noun`, snake_case), one-sentence purpose, arguments with types.
- What it returns. Aggregate where possible. Never return member/patient identifiers; any
  patient count goes through `suppress_small_count`.
- Which tables/columns it needs. If a column is missing from `DEFAULT_MAPPING` in
  `mcp_server/schema_mapping.py`, add it there (and to the RDS schema and the generator if the
  demo data lacks it).

## 2. Implement
1. **SQL**: add a `QueryTemplate` to `mcp_server/queries.py` and to `ALL_TEMPLATES`. Single
   SELECT/WITH, `${...}` identifiers only from the mapping, `%(name)s` binds for every user
   value, `LIMIT %(row_limit)s` when multiple rows are possible. No semicolons or comments.
2. **Validation**: reuse `validate_npi` / `validate_date_range`; put new validators in
   `mcp_server/validation.py`, raising `ToolInputError` with a safe message.
3. **Tool function** in `mcp_server/tools/`, following the existing pattern:
   validate → `ctx.db.execute(...)` → `normalize_row` → minimize/suppress.
4. **Register** an `@mcp.tool` wrapper in `mcp_server/server.py` with a precise description
   (the model picks tools from it) that calls `_run("<name>", ...)`.
5. **Allowlist**: append the name to `tool_allowlist` in `app/core/config.py` and to
   `TOOL_ALLOWLIST` in `.env.example`. Deployed `.env` files need it too.
6. **Fake model**: extend `_plan` and `_summarize` in `app/agent/fake_bedrock.py` so the
   no-credentials demo can route to the tool and describe its result.

## 3. Prove it
- Tests in `tests/test_mcp_tools.py`: happy path, validation errors, empty result,
  suppression, row limit/truncation. `tests/test_sql_safety.py` covers the new template
  automatically; make sure it passes.
- Add 2–3 lines to `evals/questions.jsonl` with `expect_tools`.
- Run the `preflight` skill, then commit and push (the secret scan runs automatically).
- If the demo is deployed, offer the `deploy-demo` skill and ask a new eval question against it.

# CLAUDE.md — Natural Language Snowflake Data Assistant

## Mission
Build a working, production-minded web application where authenticated users ask natural-language questions about healthcare claims and provider data in Snowflake. A web chat UI calls a FastAPI `POST /chat` endpoint; the backend orchestrates AWS Bedrock Converse tool use, invokes controlled tools on a separate long-running MCP server, queries Snowflake through predefined parameterized SQL, and returns a grounded response to the UI.

Implement runnable code, tests, documentation, local development setup, and AWS deployment scaffolding. Do not stop at diagrams or pseudocode.

## Compliance constraints (read first)
Claims data is PHI. Every design choice must assume HIPAA obligations apply.
- Use only HIPAA-eligible AWS services under the organization's BAA (ECS Fargate, ALB, Bedrock, DynamoDB, CloudWatch Logs, Secrets Manager, KMS). Snowflake must be an edition/agreement approved for PHI (typically Business Critical).
- Bedrock model invocation logging must be disabled, or routed only to an encrypted, access-controlled destination approved for PHI.
- Never log user messages, model output, tool arguments containing identifiers, or query results. Log tool names, statuses, durations, and request IDs only.
- Persisted conversation state is PHI: encrypt at rest with KMS, set a TTL, and persist the minimum needed (see "Conversation state").
- Minimize data returned by tools: aggregate where possible, return no member/patient identifiers, and apply small-cell suppression (counts of patients between 1 and the configured threshold, default 11, are reported as suppressed).

## Confirmed architectural decisions
- Frontend: minimal static chat page served by FastAPI. No build step, no framework.
- Backend: Python 3.12, FastAPI on Uvicorn; `POST /chat` is the main entry point.
- LLM: AWS Bedrock Converse API via `boto3`, IAM credentials, configurable region and model ID. The model ID setting must accept either a foundation model ID or an inference profile ID/ARN (many current models require inference profiles).
- Tool integration: MCP client in the FastAPI agent talks to a separately running, long-lived MCP server over **Streamable HTTP**. The server runs in **stateless mode** (`stateless_http=True`, JSON responses) so it scales horizontally without sticky sessions.
- Tool discovery: the agent calls `list_tools` on the MCP server and **intersects the result with a configured allowlist**. Do not duplicate tool schemas in the API service. Cache the discovered list with a TTL.
- Data access: the MCP server executes predefined, parameterized SQL templates against Snowflake. The model never submits SQL.
- Service-to-service auth: API -> MCP calls carry a bearer token from Secrets Manager, checked in constant time by the MCP server, in addition to security-group restrictions.
- AWS hosting: HTTPS -> public ALB -> ECS Fargate FastAPI tasks (port 8000). MCP server is a separate Fargate service reachable only internally via **ECS Service Connect** (`http://mcp:8001/mcp`). No Nginx, no API Gateway.
- User authentication: **ALB `authenticate-oidc` action** (Cognito or the corporate IdP). FastAPI verifies the ALB-signed `x-amzn-oidc-data` JWT (ES256, signer ARN check, public key from the regional ALB key endpoint). `AUTH_MODE=none` is allowed only outside production; the app refuses to start in production without auth.
- Authorization: v1 uses a single read-only Snowflake service role, meaning every authenticated user can query every provider. This is a documented, explicit decision. The user's identity (`sub`) is passed through the agent to the tool layer so per-user scoping (tool-level checks or Snowflake row access policies) can be added without redesign.
- Conversation state: stored behind a `ConversationStore` abstraction. In-memory for local only; **DynamoDB with TTL and KMS encryption** for deployed environments. Conversations are bound to the owning user; an ID owned by someone else starts a new conversation.

## Request lifecycle
1. User enters a question in the UI (already authenticated by the ALB in deployed environments).
2. UI sends `POST /chat` with message and optional conversation ID.
3. FastAPI validates the request, resolves the user, and calls `agent.process()` under a per-request time budget.
4. Agent loads prior turns from the conversation store, discovers allowed tools, and calls Bedrock Converse with the system prompt, messages, and tool specs.
5. On `stopReason == "tool_use"`, the agent rejects any tool not in the allowlist, validates that arguments are an object, and calls the MCP server (bounded parallelism, per-tool timeout).
6. MCP server authenticates the caller, validates arguments, renders a predefined SQL template with configured identifiers, binds parameters, executes with a statement timeout and row limit, normalizes and minimizes the result, and returns structured JSON.
7. Agent returns `toolResult` blocks to Bedrock, iterating up to `MAX_TOOL_ROUNDS`.
8. Agent returns the final grounded text plus a tool-call summary (name, status, duration).
9. FastAPI persists the user/assistant text turn and returns JSON. Errors are mapped to safe messages with a request ID.

## Timeouts must nest
`REQUEST_TIMEOUT_SECONDS` (default 90) must be shorter than the ALB idle timeout (Terraform default 120). Each Bedrock call and tool call uses the smaller of its own timeout and the remaining request budget. A request that exceeds the budget returns 504 with a safe message rather than letting the ALB drop it. ConverseStream + server-sent events is the documented future path if long turns become common; v1 returns plain JSON.

## Repository structure
```
CLAUDE.md  README.md  .env.example  pyproject.toml  Dockerfile  docker-compose.yml  Makefile
app/                       # FastAPI service (no Snowflake code here)
  main.py                  # app factory, lifespan wiring, exception handlers, static UI
  api/routes/chat.py       # POST /chat
  api/routes/health.py     # GET /health, GET /ready
  api/deps.py  api/errors.py
  auth/                    # none | alb_oidc user resolution
  schemas/chat.py
  agent/                   # Bedrock agent loop, tool registry, prompts, errors, fake model
  mcp_client/client.py     # Streamable HTTP MCP client
  storage/                 # ConversationStore: memory, dynamodb
  core/config.py  core/logging.py  core/middleware.py
  static/index.html  static/app.js  static/styles.css
mcp_server/                # MCP service (only place that touches the database)
  server.py                # FastMCP app, auth middleware, /health
  config.py  validation.py  schema_mapping.py  queries.py  results.py
  db/base.py  db/snowflake_client.py  db/postgres_client.py  db/sqlite_mock.py   # DB_BACKEND selects
  tools/provider_tools.py  tools/claims_tools.py
tests/   evals/   docs/STATUS.md
scripts/                   # generate_fake_npi_data.py, check_secrets.py, run_evals.py, install_hooks.sh
infra/terraform/ (production design)  infra/ec2-demo/ (running demo)  infra/rds/  infra/snowflake/
.claude/                   # Claude Code config: settings + hooks, agents/, skills/ (see "Working with Claude Code")
.githooks/  .github/workflows/ci.yml
```
Keep the FastAPI app, MCP server, and Snowflake layers separate. `app/` must never import `mcp_server/` (tests may).

## API contract
`POST /chat`
```json
{"message": "How many claims were submitted for provider 1234567893 in 2025?", "conversation_id": null}
```
```json
{"answer": "...", "conversation_id": "uuid", "request_id": "uuid",
 "tool_calls": [{"name": "get_provider_claim_summary", "status": "success", "duration_ms": 142}]}
```
Errors: `{"error": {"code": "...", "message": "..."}, "request_id": "..."}`. Validation errors must not echo request input. `tool_calls` never contains arguments, rows, credentials, or exception text.
`GET /health` is a cheap liveness check for the ALB. `GET /ready` checks configuration and that the tool list can be loaded (short timeout).

## MCP tools and Snowflake
Illustrative tools (adapt to the real schema):
- `get_provider_by_npi(npi)`
- `get_provider_claim_summary(npi, start_date, end_date)`
- `get_claim_counts_by_status(start_date, end_date)`

Rules:
- Table and column names live in `mcp_server/schema_mapping.py` defaults, overridable by a JSON file (`SCHEMA_MAPPING_PATH`). Identifiers are validated against a strict regex before rendering. Document exactly what to update once the real schema arrives.
- SQL templates live only in `mcp_server/queries.py`, must be single `SELECT`/`WITH` statements, contain no semicolons, and take user values only through bind parameters. Identifier substitution happens once at startup from config, never from user input.
- Validate inputs: NPI is 10 digits with a valid Luhn check digit (80840 prefix); dates are ISO `YYYY-MM-DD`, start <= end, range capped (default 731 days).
- Enforce row limits by `LIMIT` and `fetchmany(limit + 1)` to report truncation. Set `STATEMENT_TIMEOUT_IN_SECONDS` at session level and a per-call timeout.
- Tag every query with `QUERY_TAG` containing the request ID so chat turns can be traced in `QUERY_HISTORY`.
- Snowflake auth: **key-pair** for a `TYPE = SERVICE` user (single-factor passwords are being phased out by Snowflake; allow password only for local experiments). Role is read-only, granted `SELECT` on specific views that expose only needed columns. Provide the setup SQL in `infra/snowflake/`.
- The connector is synchronous: run it in a bounded thread pool with a small connection pool sized to warehouse capacity. Document warehouse size and auto-suspend considerations.
- Mock mode runs the **same SQL templates** against a seeded in-memory SQLite database, so templates are exercised locally and in tests.
- Tool errors raised to the client contain only safe, user-facing text.

## Bedrock agent requirements
- Converse API, system prompt as a separate `system` block, tool specs from the discovered allowlisted tools.
- Correct `toolUse` -> `toolResult` pairing in a single following user message; JSON tool results; `status: "error"` for failures.
- Iterate until `end_turn` or `MAX_TOOL_ROUNDS`; handle `max_tokens`, guardrail/content-filter stops.
- Run blocking boto3 calls in a **dedicated thread pool sized to `BEDROCK_MAX_CONCURRENCY`** (the default executor is only `cpu + 4` workers, i.e. 5 on a 1-vCPU task). Use botocore `adaptive` retry mode with bounded attempts rather than hand-rolled backoff.
- Map throttling, timeouts, access/validation errors to typed agent errors with safe messages.
- Treat all tool output as untrusted data. The system prompt says so explicitly, and the allowlist plus server-side validation enforce policy in code regardless of what the model is told.
- State clearly when a question cannot be answered with the available tools. Never invent values.

## Conversation state
Persist only user text and final assistant text per turn, not tool results. This keeps PHI in the store to what the user already saw, keeps items small, and avoids replaying orphaned `toolUse` blocks. Keep the last `MAX_HISTORY_TURNS` turns. Store a turn only after a successful response.

## Mock mode
Two independent switches:
- `MOCK_BEDROCK=true`: a deterministic fake Converse client that parses NPIs/dates/keywords and emits real `toolUse` blocks, then summarizes the tool results. No AWS credentials needed. Its answers are prefixed so nobody mistakes them for model output.
- `MOCK_SNOWFLAKE=true` (MCP server): seeded SQLite running the real templates.
Docker Compose with both enabled must demonstrate the full UI -> API -> fake Bedrock -> MCP -> SQL -> API -> UI cycle with zero credentials.

## UI requirements
Minimal, responsive, accessible chat page: input, send button, Enter to send (Shift+Enter newline), distinct user/assistant messages, loading state, error state with retry, conversation ID kept in `sessionStorage`, assistant text rendered via `textContent` only, a small trail of tool calls under each answer, light and dark themes. No credentials client-side. Same-origin API calls; CORS closed by default.

## Configuration and security
Environment variables locally; Secrets Manager injected as ECS secrets in AWS. `.env.example` lists every setting with placeholders. Never commit secrets. IAM task roles: API role may call `bedrock:InvokeModel` on configured model/profile ARNs and read/write the DynamoDB table; MCP role needs no AWS data permissions beyond its injected secrets. Structured JSON logs with request IDs and key-based redaction.

## Concurrency and deployment
- No blocking calls on the event loop.
- MCP server is long-running; the API opens one MCP client session per chat request and reuses it for that request's tool calls.
- Separate Docker targets for `api` and `mcp`; non-root containers.
- Terraform: ALB (HTTPS, HTTP->HTTPS redirect, optional OIDC action, idle timeout), target group on 8000 with `/health`, ECS cluster, two Fargate services, Service Connect namespace, security groups, IAM roles, log groups, DynamoDB table, API autoscaling. VPC, subnets, certificate, and IdP settings are inputs.

## Testing, evals, and developer experience
- Unit tests: request validation, allowlist enforcement, tool loop, error mapping, SQL template safety, identifier validation, input validation, row limits, small-cell suppression, conversation ownership.
- Integration tests: API with fake Bedrock and in-process tools; MCP server + real MCP client over HTTP (marked `integration`).
- **Evals:** `evals/questions.jsonl` with expected tool calls, run by `scripts/run_evals.py` against a running API (real Bedrock on demand). Run before changing models or prompts.
- `make setup | lint | typecheck | test | run-mcp | run-api | up | evals`.
- Do not claim production readiness without authentication enabled, DynamoDB conversation storage, real schema mappings, and environment-specific infrastructure values.

## Implementation sequence with checkpoints
1. Inspect the repo; summarize reusable code.
2. Plan and list assumptions; ask only for blocking details (real schema, auth method, IdP).
3. FastAPI `/chat`, schemas, config, logging, error handling.
4. MCP server with validated tools, SQL templates, SQLite mock, auth middleware.
5. Agent loop, fake Bedrock, real Bedrock client, MCP client, conversation store.
6. UI wired end to end.
7. **Checkpoint: stop and have a human run the mock end-to-end slice before continuing.**
8. Tests, evals, Docker Compose, docs.
9. Terraform and Snowflake setup scripts.
10. Run tests, fix failures, and report exactly what works, what is mocked, and what configuration remains.

## Definition of done
A developer clones the repo, copies `.env.example` to `.env`, runs `docker compose up`, opens `http://localhost:8000`, asks a question, and sees an answer produced by a full fake-Bedrock -> MCP -> SQL cycle with the tool trail shown. With Bedrock access, real schema mappings, Snowflake key-pair credentials, and auth configured, the same code runs approved real queries. Tests pass; deployment steps are explicit.

## Repository hygiene (always)
This repository is public. Before every commit and push:
- Run the secret scan (`make check-secrets`, or it runs automatically as the pre-commit hook once `make hooks` has been run in the clone). Never bypass it with `--no-verify`; fix the finding instead.
- Never commit credentials, tokens, private keys, `.env`, `.pem`, Terraform state or real `.tfvars`, generated data (`data/`), or real AWS identifiers (account IDs, endpoints, secret ARNs, resource IDs, IPs). Use placeholders such as `111122223333` and `REPLACE_...` in code and docs.
- Keep environment-specific values in git-ignored files (`.env`, `.secrets-denylist`, filled-in user data) or in AWS (Secrets Manager, task definitions).
- `.secrets-denylist` (git-ignored, one literal per line) lists this environment's private identifiers so the scan blocks them; keep it up to date when new AWS resources are created.

## Working with Claude Code in this repo
- **Start of a session:** read `docs/STATUS.md` (public status) and the `project-status` memory (private details, if present) before proposing work.
- **AWS cost control:** the demo's EC2 server and RDS database are tagged `Project=chatbot-demo` and stay stopped between sessions. `scripts/aws_demo.sh start|stop|status` (or `make aws-start` / `aws-stop` / `aws-status`) controls them. On the owner's Mac, `.claude/settings.local.json` (git-ignored) runs `start` on SessionStart and `stop` on SessionEnd. Before ending a session, make sure they are stopped (`make aws-status`) — SessionEnd does not fire if the app is force-quit. The database needs ~5 minutes after start, and the server's public IP changes on every start.
- **Skills** (`.claude/skills/`): `preflight` before any commit/PR; `add-mcp-tool` for every new question type; `deploy-demo` to redeploy and verify the EC2 demo (confirm with the user first); `update-status` at the end of a work session.
- **Subagents** (`.claude/agents/`): `security-reviewer` (read-only review of a diff against the rules in this file) and `aws-inspector` (read-only AWS state, exposure, and cost). Use them for an independent read rather than reviewing your own change.
- **Guardrails:** `.claude/settings.json` denies reading `.env`, key files, and `.secrets-denylist`, and a PreToolUse hook (`.claude/hooks/guard_git.py`) runs the secret scan before `git commit`/`git push` and blocks `--no-verify` and force pushes. CI (`.github/workflows/ci.yml`) repeats lint, types, tests, and the secret scan on every push.
- **AWS changes** (creating, modifying, or deleting resources) always need the user's explicit approval for that action. Keep resource IDs out of the repo.
- **Commands for the user:** say where each runs (their Mac vs AWS CloudShell); prefer doing AWS steps yourself via the CLI/SSM with approval.

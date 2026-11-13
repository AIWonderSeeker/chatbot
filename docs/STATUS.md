# Project status

_Last updated: 2026-11-13. Public file: no account IDs, endpoints, ARNs, IPs, or credentials.
Environment-specific details live outside the repo (your `.env`, `.secrets-denylist`, and AWS itself)._

## In one paragraph

A natural-language assistant over healthcare provider and claims data. A web chat page calls
a FastAPI service; an agent loop asks a model which of three controlled tools to use; the tools
run on a separate MCP server that is the only component allowed to touch the database and only
runs pre-written, parameterized, read-only SQL. Today it runs as a **demo on a single EC2
instance**, answering from **synthetic data in Amazon RDS PostgreSQL**, with a **fake model**
standing in until Amazon Bedrock access is enabled.

## What works

| Area | State | Where |
| --- | --- | --- |
| Chat UI, `POST /chat`, health/readiness | Working | `app/` |
| Agent loop (tool use, allowlist, time budgets, safe errors) | Working | `app/agent/` |
| MCP server with 3 tools, input validation, small-cell suppression | Working | `mcp_server/` |
| Database backends: SQLite mock, Snowflake, **PostgreSQL / RDS** | Mock + RDS verified; Snowflake untested live | `mcp_server/db/` |
| Synthetic data generator + RDS schema/loader | Working; data loaded in RDS | `scripts/generate_fake_npi_data.py`, `infra/rds/` |
| Demo hosting (one EC2, Docker Compose) | Verified end to end; **stopped between sessions** to save cost (`make aws-start` / `aws-stop`) | `infra/ec2-demo/`, `scripts/aws_demo.sh` |
| Fake model (`MOCK_BEDROCK=true`) | In use | `app/agent/fake_bedrock.py` |
| Real model (Claude on Bedrock) | Code ready; account access pending | `app/agent/bedrock_client.py` |
| Production hosting (ECS Fargate, HTTPS ALB + OIDC, DynamoDB) | Terraform written, never applied | `infra/terraform/` |
| Tests | 158 unit + 3 integration, all passing | `tests/` |
| Secret scanning (pre-commit hook + CI) | Active | `scripts/check_secrets.py`, `.githooks/` |

## Verified answers (demo, synthetic data)

| Question | Answer from RDS |
| --- | --- |
| Who is provider 1234567893? | Nadia Hughes, Individual, Obstetrics & Gynecology, MD |
| Claims for 1234567893 in 2025 | 21 claims, 11 patients, $28,126.64 billed |
| Claim counts by status, Q1 2025 | 1,263 total (764 paid, 194 denied, 153 pending, 97 adjusted, 55 rejected) |
| Claims for 1003000035, 2024–2025 | 6 claims, patient count suppressed (`<11`) |

## Known limitations

- Demo hosting has **no login, plain HTTP, and in-memory conversations**. Synthetic data only; never real PHI.
- Only three question types (provider lookup, provider claim summary, claim counts by status).
- The fake model understands phrasings close to the examples; real Claude is needed for free-form questions.
- The demo connects to RDS as the master user (read-only session). Real data needs a dedicated login granted `nl_assistant_reader`.

## Next steps (in suggested order)

1. **Enable Claude on Bedrock**: submit Anthropic's one-time use-case form in the Bedrock console, grant the instance role `bedrock:InvokeModel` on the chosen inference profile, set `MOCK_BEDROCK=false` and `BEDROCK_MODEL_ID`. See `infra/ec2-demo/README.md`.
2. **Tighten the database security group**: its PostgreSQL rule is open to `0.0.0.0/0`; restrict it to the app's security group.
3. **Dedicated read-only DB login** instead of the master user.
4. **More tools** for new question types (each = SQL template + tool + tests + eval questions).
5. **Production deployment** with `infra/terraform` (needs a domain, ACM certificate, and an identity provider).

## How to resume

```bash
cd chatbot && make setup && make hooks      # Python 3.12 venv, deps, secret-scan hook
make test && make check-secrets             # should be all green
```

Run locally with no credentials: `make run-mcp` and `make run-api` in two terminals, then open
http://localhost:8000. Redeploy the demo after pushing: see `infra/ec2-demo/README.md` → Operate.

## Change log

| Date | Change |
| --- | --- |
| 2026-11-13 | Initial import; Python 3.12; lint/type fixes; ALB-auth and DynamoDB tests |
| 2026-11-13 | Synthetic NPI/claims generator; RDS PostgreSQL schema and loader |
| 2026-11-13 | PostgreSQL backend for the MCP server; EC2 demo deployed against RDS |
| 2026-11-13 | Secret scanning hook; Claude Code project config (agents, skills, hooks); CI |
| 2026-11-13 | AWS start/stop script; demo resources stopped between sessions |

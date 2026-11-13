---
name: preflight
description: Run every check this repo requires before a commit or push — lint, format, types, unit + integration tests, secret scan, and an independent security review of the diff. Use before committing, before opening a PR, or when asked "is this ready?".
---

# Preflight

Run these from the repo root and report each result (pass/fail with the failing output).
Run all steps and report all results; don't stop at the first failure unless later steps depend on it.

1. `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`
   - If formatting fails, run `.venv/bin/ruff format .` and say which files changed.
2. `.venv/bin/mypy app mcp_server`
3. `.venv/bin/pytest -q`, then `.venv/bin/pytest -q -m integration`
4. `python3 scripts/check_secrets.py --all` (and without `--all` if anything is staged)
5. Delegate a review of the pending diff to the `security-reviewer` subagent. Give it the diff
   scope (`git diff HEAD` or the commit range) and wait for its findings.
6. If `docs/STATUS.md` states test counts or capabilities that the diff changes, say it needs
   the `update-status` skill.

Finish with a short verdict: ready / not ready, and exactly what blocks it. Never commit or push
from this skill.

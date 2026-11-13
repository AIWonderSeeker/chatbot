---
name: update-status
description: Refresh the project's status records at the end of a work session — the public docs/STATUS.md (no private identifiers) and the private project-status memory (may hold AWS IDs). Use when the user says "save where we are", "update the status", or after finishing a milestone.
---

# Update status

Two records for two audiences:

| Record | Visibility | May contain |
| --- | --- | --- |
| `docs/STATUS.md` | Public (GitHub) | Capabilities, test counts, verified behavior, limitations, next steps, change log. **No** account IDs, endpoints, ARNs, resource IDs, IPs, or emails. |
| Memory `project-status` | Local to this Mac | Everything needed to resume, including AWS resource IDs and endpoints. |

Steps:
1. Gather facts; don't guess: `git log --oneline -15`, `.venv/bin/pytest -q | tail -1`,
   `.venv/bin/pytest -q -m integration | tail -1`, and, if AWS matters, the `aws-inspector`
   subagent for the live state.
2. Update `docs/STATUS.md`: the "Last updated" date, the "What works" table, test counts,
   "Known limitations", "Next steps", and one change-log row per meaningful change.
3. Update the `project-status` memory file in the auto-memory directory: status date, what's
   done, AWS resources and how to reach them, open items. Keep its one-line pointer in
   `MEMORY.md` accurate.
4. `python3 scripts/check_secrets.py --all` must pass; then commit `docs/STATUS.md` and push.
5. Tell the user in two or three lines what changed in each record.

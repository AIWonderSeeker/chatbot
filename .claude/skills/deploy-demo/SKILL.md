---
name: deploy-demo
description: Redeploy the single-EC2 demo from the latest pushed commit and verify it end to end (health, readiness, sample chat answers). Use after pushing changes the demo should run. Changes a live AWS instance, so confirm with the user first.
---

# Deploy the EC2 demo

This changes a running AWS instance: confirm with the user before step 3. Never put instance
IDs, IPs, account IDs, or ARNs in repo files; keep them in the conversation or memory only.

1. **Preconditions**: working tree clean and `main` pushed (`git status -sb` shows nothing
   ahead). AWS CLI works: `aws sts get-caller-identity --output json` with the user's SSO
   profile (`AWS_PROFILE`). If the SSO session expired, ask the user to run `aws sso login`.
2. **Find the instance by tag**, not a remembered ID:
   `aws ec2 describe-instances --filters Name=tag:Project,Values=chatbot-demo Name=instance-state-name,Values=running --query 'Reservations[].Instances[].[InstanceId,PublicIpAddress]' --output text`
   If none is running, stop and tell the user (it may be stopped to save cost).
3. **Redeploy through SSM** (no SSH needed):
   `aws ssm send-command --instance-ids <id> --document-name AWS-RunShellScript --timeout-seconds 600 --parameters 'commands=["cd /opt/chatbot && git pull -q && docker compose -f docker-compose.yml -f infra/ec2-demo/compose.ec2.yml up -d --build 2>&1 | tail -5 && git log --oneline -1"]' --query Command.CommandId --output text`
   then `aws ssm wait command-executed` and `aws ssm get-command-invocation`. The commit it
   prints must match local `HEAD`.
4. **Verify from the Mac.** The security group only allows the user's IP; if requests time
   out, the user's IP may have changed.
   - `curl -s http://<ip>:8000/health` returns `{"status":"ok"}`
   - `curl -s http://<ip>:8000/ready` shows every check `ok`
   - POST two or three questions from `evals/questions.jsonl` to `/chat`; every
     `tool_calls[].status` must be `success`.
5. **If a tool call fails**, read the MCP logs through SSM (`docker compose logs --tail 50 mcp`);
   failures log `error_type` and a value-free `error`. Fix it in code, push, and repeat. Don't
   hand-edit files on the instance.
6. Report the commit deployed, the verification results, and the URL.

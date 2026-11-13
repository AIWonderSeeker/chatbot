#!/usr/bin/env bash
# Start, stop, or check the demo's AWS resources (EC2 app server + RDS database) to control cost.
#
#   scripts/aws_demo.sh status            # state of each resource, app URL when running
#   scripts/aws_demo.sh start [--wait]    # start; --wait blocks until the app answers /health
#   scripts/aws_demo.sh stop  [--wait]    # stop;  --wait blocks until both are stopped
#
# Resources are found by tag (Project=chatbot-demo, override with CHATBOT_AWS_TAG), so no
# instance IDs or ARNs live in the repo. Uses the AWS CLI with your current credentials
# (AWS_PROFILE / CHATBOT_AWS_PROFILE) and region (AWS_REGION, default us-east-1).
#
# --hook: never fail (exit 0) and keep output short; used by the Claude Code SessionStart /
# SessionEnd hooks in .claude/settings.local.json.
#
# Notes: a stopped RDS instance is started again by AWS automatically after 7 days. The EC2
# public IP changes on every start; the security group only allows your own IP on port 8000.
set -uo pipefail

ACTION="${1:-status}"; shift || true
WAIT=0; HOOK=0
for arg in "$@"; do
  case "$arg" in
    --wait) WAIT=1 ;;
    --hook) HOOK=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

export AWS_PROFILE="${CHATBOT_AWS_PROFILE:-${AWS_PROFILE:-}}"
[ -z "$AWS_PROFILE" ] && unset AWS_PROFILE
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_PAGER=""
TAG="${CHATBOT_AWS_TAG:-chatbot-demo}"

fail() {  # in hook mode report and exit 0 so the session is never blocked
  echo "aws-demo: $*" >&2
  if [ "$HOOK" = 1 ]; then exit 0; else exit 1; fi
}

command -v aws >/dev/null 2>&1 || fail "AWS CLI not installed"
aws sts get-caller-identity --output text --query Account >/dev/null 2>&1 \
  || fail "not signed in to AWS (run: aws sso login${AWS_PROFILE:+ --profile $AWS_PROFILE})"

ARNS=$(aws resourcegroupstaggingapi get-resources --output text \
  --tag-filters "Key=Project,Values=$TAG" --resource-type-filters ec2:instance rds:db \
  --query 'ResourceTagMappingList[].ResourceARN' 2>/dev/null) || fail "could not list tagged resources"
EC2_IDS=$(tr '\t' '\n' <<<"$ARNS" | sed -n 's#^arn:aws:ec2:.*:instance/##p')
RDS_IDS=$(tr '\t' '\n' <<<"$ARNS" | sed -n 's#^arn:aws:rds:.*:db:##p')
[ -z "$EC2_IDS$RDS_IDS" ] && fail "no resources tagged Project=$TAG"

ec2_state() { aws ec2 describe-instances --instance-ids "$1" --output text --query 'Reservations[0].Instances[0].State.Name'; }
ec2_ip()    { aws ec2 describe-instances --instance-ids "$1" --output text --query 'Reservations[0].Instances[0].PublicIpAddress'; }
rds_state() { aws rds describe-db-instances --db-instance-identifier "$1" --output text --query 'DBInstances[0].DBInstanceStatus'; }

status() {
  for id in $RDS_IDS; do echo "database $id: $(rds_state "$id")"; done
  for id in $EC2_IDS; do
    s=$(ec2_state "$id"); ip=$(ec2_ip "$id")
    if [ "$s" = running ] && [ "$ip" != None ]; then
      echo "app server $id: running -> http://$ip:8000"
    else
      echo "app server $id: $s"
    fi
  done
}

case "$ACTION" in
  status)
    status
    ;;
  start)
    # Database first: the app's queries need it, and RDS takes the longest (~5 min).
    for id in $RDS_IDS; do
      [ "$(rds_state "$id")" = stopped ] && aws rds start-db-instance --db-instance-identifier "$id" --output text --query 'DBInstance.DBInstanceStatus' >/dev/null && echo "database $id: starting"
    done
    for id in $EC2_IDS; do
      [ "$(ec2_state "$id")" = stopped ] && aws ec2 start-instances --instance-ids "$id" --output text >/dev/null && echo "app server $id: starting"
    done
    if [ "$WAIT" = 1 ]; then
      for id in $RDS_IDS; do aws rds wait db-instance-available --db-instance-identifier "$id"; done
      for id in $EC2_IDS; do
        aws ec2 wait instance-running --instance-ids "$id"
        ip=$(ec2_ip "$id")
        for _ in $(seq 1 40); do
          curl -s -m 5 -o /dev/null -w '%{http_code}' "http://$ip:8000/health" 2>/dev/null | grep -q 200 && break
          sleep 10
        done
      done
    fi
    status
    [ "$HOOK" = 1 ] && echo "aws-demo: resources start in the background; the database takes ~5 minutes. Check with: make aws-status"
    ;;
  stop)
    for id in $EC2_IDS; do
      case "$(ec2_state "$id")" in running|pending) aws ec2 stop-instances --instance-ids "$id" --output text >/dev/null && echo "app server $id: stopping" ;; esac
    done
    for id in $RDS_IDS; do
      [ "$(rds_state "$id")" = available ] && aws rds stop-db-instance --db-instance-identifier "$id" --output text --query 'DBInstance.DBInstanceStatus' >/dev/null && echo "database $id: stopping"
    done
    if [ "$WAIT" = 1 ]; then
      for id in $EC2_IDS; do aws ec2 wait instance-stopped --instance-ids "$id"; done
      for id in $RDS_IDS; do
        for _ in $(seq 1 60); do [ "$(rds_state "$id")" = stopped ] && break; sleep 10; done
      done
    fi
    status
    ;;
  *)
    echo "usage: $0 status|start|stop [--wait] [--hook]" >&2; exit 2 ;;
esac
exit 0

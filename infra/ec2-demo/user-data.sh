#!/bin/bash
# EC2 user data for the single-instance demo (Amazon Linux 2023).
# Installs Docker, clones the repo, writes .env, and starts API + MCP with Docker Compose.
#
# Replace the __PLACEHOLDERS__ before use (infra/ec2-demo/README.md shows how). The instance
# needs: outbound internet (public IP or NAT), network access to the database on 5432, an
# instance role allowed to read PG_SECRET_ARN, and IMDSv2 hop limit 2 so containers can use
# that role.
set -euxo pipefail
exec > >(tee /var/log/chatbot-setup.log) 2>&1

REPO_URL="__REPO_URL__"
PG_HOST="__PG_HOST__"
PG_DATABASE="__PG_DATABASE__"
PG_USER="__PG_USER__"
PG_SECRET_ARN="__PG_SECRET_ARN__"
AWS_REGION="__AWS_REGION__"
COMPOSE_VERSION="v2.29.7"

dnf install -y docker git
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

mkdir -p /opt/chatbot-certs
curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o /opt/chatbot-certs/global-bundle.pem

git clone "$REPO_URL" /opt/chatbot
cd /opt/chatbot

umask 077
cat > .env <<ENV
ENVIRONMENT=dev
LOG_LEVEL=INFO
AWS_REGION=${AWS_REGION}

# Fake model until Bedrock access is enabled; then set MOCK_BEDROCK=false and BEDROCK_MODEL_ID.
MOCK_BEDROCK=true
BEDROCK_MODEL_ID=REPLACE_WITH_MODEL_OR_INFERENCE_PROFILE_ID

MCP_AUTH_TOKEN=$(openssl rand -hex 32)
CONVERSATION_STORE=memory
AUTH_MODE=none

DB_BACKEND=postgres
PG_HOST=${PG_HOST}
PG_PORT=5432
PG_DATABASE=${PG_DATABASE}
PG_USER=${PG_USER}
PG_SECRET_ARN=${PG_SECRET_ARN}
PG_SSLMODE=verify-full
PG_SSLROOTCERT=/certs/global-bundle.pem
SCHEMA_MAPPING_JSON={"tables":{"providers":"claims_app.providers","claims":"claims_app.claims"}}
ENV

docker compose -f docker-compose.yml -f infra/ec2-demo/compose.ec2.yml up -d --build
echo "chatbot setup complete"

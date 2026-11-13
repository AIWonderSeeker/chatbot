# Single-EC2 demo deployment

One small EC2 instance runs both containers (API on port 8000, MCP server internal) with
Docker Compose, reading provider/claims data from RDS PostgreSQL. It is a demo: plain HTTP,
no user login, in-memory conversations, and the fake model unless Bedrock is configured.
Use `infra/terraform` (ECS Fargate behind an HTTPS load balancer) for anything real, and
never point this setup at real PHI.

## What it needs

| Resource | Notes |
| --- | --- |
| IAM role + instance profile | `AmazonSSMManagedInstanceCore` (shell access via Session Manager) and `secretsmanager:GetSecretValue` on the RDS master secret only |
| Security group | Inbound TCP 8000 from your own IP (`/32`) only |
| EC2 instance | Amazon Linux 2023, `t3.small`, public subnet with a public IP (or private subnet + NAT and a tunnel), encrypted gp3 disk, **IMDSv2 with hop limit 2** so containers can use the role |
| Database reachability | The RDS security group must allow 5432 from the instance |

## Create

1. Fill in the placeholders in `user-data.sh` (keep the filled-in copy out of git):

   ```bash
   sed -e 's#__REPO_URL__#https://github.com/<owner>/chatbot.git#' \
       -e 's#__PG_HOST__#<rds-endpoint>#' -e 's#__PG_DATABASE__#postgres#' -e 's#__PG_USER__#postgres#' \
       -e 's#__PG_SECRET_ARN__#<rds-master-secret-arn>#' -e 's#__AWS_REGION__#us-east-1#' \
       infra/ec2-demo/user-data.sh > /tmp/user-data.sh
   ```

2. Launch with that file as user data, e.g.:

   ```bash
   aws ec2 run-instances --image-id <al2023-ami> --instance-type t3.small \
     --subnet-id <public-subnet> --security-group-ids <sg> --associate-public-ip-address \
     --iam-instance-profile Name=<profile> \
     --metadata-options HttpTokens=required,HttpPutResponseHopLimit=2,HttpEndpoint=enabled \
     --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=20,VolumeType=gp3,Encrypted=true}' \
     --user-data file:///tmp/user-data.sh
   ```

   The latest AL2023 AMI: `aws ssm get-parameters --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`.

3. After 5–10 minutes, open `http://<public-ip>:8000`. Setup output is in
   `/var/log/chatbot-setup.log` on the instance.

## Operate

Open a shell from the EC2 console (**Connect → Session Manager**), then:

```bash
cd /opt/chatbot
sudo docker compose ps                    # both services should be healthy
sudo docker compose logs --tail 50 mcp    # database errors show up here
# Deploy the latest code from GitHub:
sudo git pull && sudo docker compose -f docker-compose.yml -f infra/ec2-demo/compose.ec2.yml up -d --build
```

**Switch to Claude on Bedrock:** grant the role `bedrock:InvokeModel` on the inference profile and
the foundation models it routes to, then in `/opt/chatbot/.env` set `MOCK_BEDROCK=false` and
`BEDROCK_MODEL_ID=<inference-profile-id>` and rerun the `up -d` command above.

**Your IP changed:** update the security group's port 8000 rule.

## Tear down

Terminate the instance, then delete the security group and the instance profile/role
(remove the role from the profile, detach/delete its policies, delete both). RDS is untouched.

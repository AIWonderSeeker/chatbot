locals {
  prefix   = "${var.name}-${var.environment}"
  api_port = 8000
  mcp_port = 8001
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------- KMS
resource "aws_kms_key" "app" {
  description         = "${local.prefix} conversation store encryption"
  enable_key_rotation = true
}

resource "aws_kms_alias" "app" {
  name          = "alias/${local.prefix}"
  target_key_id = aws_kms_key.app.key_id
}

# ---------------------------------------------------------------- Logs
resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.prefix}/api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "mcp" {
  name              = "/ecs/${local.prefix}/mcp"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------- DynamoDB (conversation store)
resource "aws_dynamodb_table" "conversations" {
  name         = "${local.prefix}-conversations"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "conversation_id"

  attribute {
    name = "conversation_id"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.app.arn
  }
  point_in_time_recovery {
    enabled = true
  }
  deletion_protection_enabled = var.environment == "production"
}

# ---------------------------------------------------------------- Security groups
resource "aws_security_group" "alb" {
  name_prefix = "${local.prefix}-alb-"
  vpc_id      = var.vpc_id
  description = "Public ALB"
  lifecycle { create_before_destroy = true }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each          = toset(var.alb_ingress_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each          = toset(var.alb_ingress_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_api" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = local.api_port
  to_port                      = local.api_port
}

resource "aws_security_group" "api" {
  name_prefix = "${local.prefix}-api-"
  vpc_id      = var.vpc_id
  description = "FastAPI tasks"
  lifecycle { create_before_destroy = true }
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = local.api_port
  to_port                      = local.api_port
}

resource "aws_vpc_security_group_egress_rule" "api_https" {
  security_group_id = aws_security_group.api.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Bedrock, DynamoDB, ALB key endpoint, ECR, logs (prefer VPC endpoints)"
}

resource "aws_vpc_security_group_egress_rule" "api_to_mcp" {
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.mcp.id
  ip_protocol                  = "tcp"
  from_port                    = local.mcp_port
  to_port                      = local.mcp_port
}

resource "aws_security_group" "mcp" {
  name_prefix = "${local.prefix}-mcp-"
  vpc_id      = var.vpc_id
  description = "MCP server tasks (internal only)"
  lifecycle { create_before_destroy = true }
}

resource "aws_vpc_security_group_ingress_rule" "mcp_from_api" {
  security_group_id            = aws_security_group.mcp.id
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = local.mcp_port
  to_port                      = local.mcp_port
}

resource "aws_vpc_security_group_egress_rule" "mcp_https" {
  security_group_id = aws_security_group.mcp.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Snowflake (consider PrivateLink), ECR, logs"
}

# ---------------------------------------------------------------- ALB
resource "aws_lb" "this" {
  name                       = substr(local.prefix, 0, 32)
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = var.public_subnet_ids
  security_groups            = [aws_security_group.alb.id]
  idle_timeout               = var.alb_idle_timeout_seconds
  drop_invalid_header_fields = true
  enable_deletion_protection = var.environment == "production"
}

resource "aws_lb_target_group" "api" {
  name                 = substr("${local.prefix}-api", 0, 32)
  port                 = local.api_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  dynamic "default_action" {
    for_each = var.oidc == null ? [] : [1]
    content {
      type  = "authenticate-oidc"
      order = 1
      authenticate_oidc {
        issuer                     = var.oidc.issuer
        authorization_endpoint     = var.oidc.authorization_endpoint
        token_endpoint             = var.oidc.token_endpoint
        user_info_endpoint         = var.oidc.user_info_endpoint
        client_id                  = var.oidc.client_id
        client_secret              = var.oidc.client_secret
        scope                      = var.oidc.scope
        session_timeout            = var.oidc.session_timeout
        on_unauthenticated_request = "authenticate"
      }
    }
  }

  default_action {
    type             = "forward"
    order            = var.oidc == null ? 1 : 2
    target_group_arn = aws_lb_target_group.api.arn
  }

  lifecycle {
    precondition {
      condition     = var.environment != "production" || var.oidc != null
      error_message = "Production requires ALB OIDC authentication (var.oidc)."
    }
  }
}

# ---------------------------------------------------------------- ECS
resource "aws_ecs_cluster" "this" {
  name = local.prefix
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_service_discovery_http_namespace" "this" {
  name        = "${local.prefix}.internal"
  description = "Service Connect namespace (API -> MCP)"
}

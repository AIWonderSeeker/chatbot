locals {
  log_config = {
    api = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "api"
      }
    }
    mcp = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.mcp.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "mcp"
      }
    }
  }
  app_environment = var.environment == "production" ? "production" : var.environment
}

# ---------------------------------------------------------------- MCP server
resource "aws_ecs_task_definition" "mcp" {
  family                   = "${local.prefix}-mcp"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.mcp_cpu
  memory                   = var.mcp_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.mcp_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "mcp"
    image     = var.mcp_image
    essential = true
    # The Snowflake connector writes an OCSP cache under ~/.cache, so the root
    # filesystem stays writable here. Mount an ephemeral volume if you lock it down.
    readonlyRootFilesystem = false
    portMappings = [{
      name          = "mcp"
      containerPort = local.mcp_port
      protocol      = "tcp"
      appProtocol   = "http"
    }]
    environment = [
      { name = "ENVIRONMENT", value = local.app_environment },
      { name = "MOCK_SNOWFLAKE", value = "false" },
      { name = "MCP_PORT", value = tostring(local.mcp_port) },
      { name = "SNOWFLAKE_ACCOUNT", value = var.snowflake.account },
      { name = "SNOWFLAKE_USER", value = var.snowflake.user },
      { name = "SNOWFLAKE_ROLE", value = var.snowflake.role },
      { name = "SNOWFLAKE_WAREHOUSE", value = var.snowflake.warehouse },
      { name = "SNOWFLAKE_DATABASE", value = var.snowflake.database },
      { name = "SNOWFLAKE_SCHEMA", value = var.snowflake.schema },
      { name = "SNOWFLAKE_AUTH_METHOD", value = "keypair" },
      { name = "SCHEMA_MAPPING_JSON", value = var.schema_mapping_json },
    ]
    secrets = [
      { name = "MCP_AUTH_TOKEN", valueFrom = var.mcp_auth_token_secret_arn },
      { name = "SNOWFLAKE_PRIVATE_KEY_PEM", valueFrom = var.snowflake_private_key_secret_arn },
    ]
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:${local.mcp_port}/health',timeout=2)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
    logConfiguration = local.log_config.mcp
  }])
}

resource "aws_ecs_service" "mcp" {
  name            = "${local.prefix}-mcp"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.mcp.arn
  desired_count   = var.mcp_desired_tasks
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.mcp.id]
    assign_public_ip = false
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
    service {
      port_name      = "mcp"
      discovery_name = "mcp"
      client_alias {
        port     = local.mcp_port
        dns_name = "mcp"
      }
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

# ---------------------------------------------------------------- API
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name                   = "api"
    image                  = var.api_image
    essential              = true
    readonlyRootFilesystem = true
    portMappings = [{
      name          = "api"
      containerPort = local.api_port
      protocol      = "tcp"
      appProtocol   = "http"
    }]
    environment = [
      { name = "ENVIRONMENT", value = local.app_environment },
      { name = "AWS_REGION", value = var.region },
      { name = "MOCK_BEDROCK", value = "false" },
      { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
      { name = "MCP_SERVER_URL", value = "http://mcp:${local.mcp_port}/mcp" },
      { name = "CONVERSATION_STORE", value = "dynamodb" },
      { name = "DYNAMODB_TABLE", value = aws_dynamodb_table.conversations.name },
      { name = "AUTH_MODE", value = var.oidc == null ? "none" : "alb_oidc" },
      { name = "ALB_ARN", value = aws_lb.this.arn },
      { name = "REQUEST_TIMEOUT_SECONDS", value = tostring(min(90, var.alb_idle_timeout_seconds - 15)) },
    ]
    secrets = [
      { name = "MCP_AUTH_TOKEN", valueFrom = var.mcp_auth_token_secret_arn },
    ]
    logConfiguration = local.log_config.api
  }])
}

resource "aws_ecs_service" "api" {
  name                              = "${local.prefix}-api"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.api.arn
  desired_count                     = var.api_min_tasks
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 30

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = local.api_port
  }

  # Client-only Service Connect: resolves http://mcp:8001.
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [desired_count] # managed by autoscaling
  }

  depends_on = [aws_lb_listener.https, aws_ecs_service.mcp]
}

# ---------------------------------------------------------------- Autoscaling (API)
resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api.name}"
  min_capacity       = var.api_min_tasks
  max_capacity       = var.api_max_tasks
}

resource "aws_appautoscaling_policy" "api_requests" {
  name               = "${local.prefix}-api-requests"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  resource_id        = aws_appautoscaling_target.api.resource_id

  # Chat requests are I/O-bound (waiting on Bedrock), so CPU is a weak signal;
  # track requests per target instead.
  target_tracking_scaling_policy_configuration {
    target_value       = 30
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.this.arn_suffix}/${aws_lb_target_group.api.arn_suffix}"
    }
  }
}

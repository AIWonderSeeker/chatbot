variable "name" {
  type    = string
  default = "nl-assistant"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be dev, staging, or production."
  }
}

variable "region" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

# ---- Network (bring your own) ----
variable "vpc_id" { type = string }
variable "public_subnet_ids" {
  type        = list(string)
  description = "Subnets for the internet-facing ALB."
}
variable "private_subnet_ids" {
  type        = list(string)
  description = "Subnets for Fargate tasks. Need egress to Bedrock, Snowflake, Secrets Manager, ECR, and CloudWatch (NAT or VPC endpoints)."
}
variable "alb_ingress_cidrs" {
  type        = list(string)
  description = "Who may reach the ALB. Prefer corporate ranges over 0.0.0.0/0."
}
variable "certificate_arn" {
  type        = string
  description = "ACM certificate for the HTTPS listener."
}
variable "alb_idle_timeout_seconds" {
  type        = number
  default     = 120
  description = "Must exceed REQUEST_TIMEOUT_SECONDS (90)."
}

# ---- User authentication at the ALB ----
variable "oidc" {
  description = "ALB authenticate-oidc settings. Required in production; null disables (dev only)."
  type = object({
    issuer                 = string
    authorization_endpoint = string
    token_endpoint         = string
    user_info_endpoint     = string
    client_id              = string
    client_secret          = string
    scope                  = optional(string, "openid email")
    session_timeout        = optional(number, 28800)
  })
  default   = null
  sensitive = true
}

# ---- Images ----
variable "api_image" { type = string }
variable "mcp_image" { type = string }

# ---- Sizing ----
variable "api_cpu" {
  type    = number
  default = 1024
}
variable "api_memory" {
  type    = number
  default = 2048
}
variable "mcp_cpu" {
  type    = number
  default = 1024
}
variable "mcp_memory" {
  type    = number
  default = 2048
}
variable "api_min_tasks" {
  type    = number
  default = 2
}
variable "api_max_tasks" {
  type    = number
  default = 10
}
variable "mcp_desired_tasks" {
  type    = number
  default = 2
}

# ---- Bedrock ----
variable "bedrock_model_id" {
  type        = string
  description = "Foundation model ID or inference profile ID/ARN passed to Converse."
}
variable "bedrock_resource_arns" {
  type        = list(string)
  description = "ARNs the API role may invoke: the inference profile and every foundation-model ARN it routes to."
}

# ---- Secrets (create and populate outside Terraform) ----
variable "mcp_auth_token_secret_arn" {
  type        = string
  description = "Secrets Manager ARN holding the API->MCP bearer token."
}
variable "snowflake_private_key_secret_arn" {
  type        = string
  description = "Secrets Manager ARN holding the Snowflake service user's PEM private key."
}
variable "secrets_kms_key_arn" {
  type        = string
  default     = null
  description = "Customer-managed KMS key used by those secrets, if any."
}

# ---- Snowflake (non-secret) ----
variable "snowflake" {
  type = object({
    account   = string
    user      = string
    role      = string
    warehouse = string
    database  = string
    schema    = string
  })
}
variable "schema_mapping_json" {
  type        = string
  default     = ""
  description = "Optional schema mapping JSON; written into the MCP container at startup."
}

variable "log_retention_days" {
  type    = number
  default = 90
}

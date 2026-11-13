environment        = "dev"
region             = "us-east-1"
vpc_id             = "vpc-REPLACE"
public_subnet_ids  = ["subnet-REPLACE-a", "subnet-REPLACE-b"]
private_subnet_ids = ["subnet-REPLACE-c", "subnet-REPLACE-d"]
alb_ingress_cidrs  = ["10.0.0.0/8"]
certificate_arn    = "arn:aws:acm:us-east-1:111122223333:certificate/REPLACE"

api_image = "111122223333.dkr.ecr.us-east-1.amazonaws.com/nl-assistant-api:0.1.0"
mcp_image = "111122223333.dkr.ecr.us-east-1.amazonaws.com/nl-assistant-mcp:0.1.0"

bedrock_model_id = "REPLACE_WITH_INFERENCE_PROFILE_ID"
bedrock_resource_arns = [
  "arn:aws:bedrock:us-east-1:111122223333:inference-profile/REPLACE",
  "arn:aws:bedrock:*::foundation-model/REPLACE",
]

mcp_auth_token_secret_arn        = "arn:aws:secretsmanager:us-east-1:111122223333:secret:nl-assistant/mcp-token-REPLACE"
snowflake_private_key_secret_arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:nl-assistant/snowflake-key-REPLACE"

snowflake = {
  account   = "myorg-myaccount"
  user      = "SVC_NL_ASSISTANT"
  role      = "NL_ASSISTANT_READER"
  warehouse = "NL_ASSISTANT_WH"
  database  = "ANALYTICS"
  schema    = "CLAIMS_APP"
}

# oidc = {
#   issuer                 = "https://idp.example.com"
#   authorization_endpoint = "https://idp.example.com/oauth2/authorize"
#   token_endpoint         = "https://idp.example.com/oauth2/token"
#   user_info_endpoint     = "https://idp.example.com/oauth2/userinfo"
#   client_id              = "REPLACE"
#   client_secret          = "REPLACE"  # pass via TF_VAR_oidc or a secrets-aware pipeline
# }

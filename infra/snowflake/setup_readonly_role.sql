-- Least-privilege setup for the NL assistant. Run as SECURITYADMIN / SYSADMIN.
-- >>> UPDATE object names to match your account before running. <<<

-- 1. Views exposing only the columns the tools need (no member names, DOB, addresses).
--    Point schema_mapping at these views, not at raw tables.
CREATE SCHEMA IF NOT EXISTS ANALYTICS.CLAIMS_APP;

CREATE OR REPLACE SECURE VIEW ANALYTICS.CLAIMS_APP.V_PROVIDERS AS
SELECT NPI, PROVIDER_NAME, SPECIALTY, STATE, ENTITY_TYPE
FROM ANALYTICS.RAW.PROVIDERS;

CREATE OR REPLACE SECURE VIEW ANALYTICS.CLAIMS_APP.V_CLAIMS AS
SELECT CLAIM_ID, RENDERING_NPI, MEMBER_ID, SUBMITTED_DATE, CLAIM_STATUS, BILLED_AMOUNT, PAID_AMOUNT
FROM ANALYTICS.RAW.CLAIMS;

-- 2. Small dedicated warehouse with aggressive auto-suspend. Size for concurrency:
--    concurrent chat users queue here. Consider multi-cluster (Enterprise+) if queuing shows up.
CREATE WAREHOUSE IF NOT EXISTS NL_ASSISTANT_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  STATEMENT_TIMEOUT_IN_SECONDS = 30
  STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = 20;

-- 3. Read-only role with SELECT on the views only.
CREATE ROLE IF NOT EXISTS NL_ASSISTANT_READER;
GRANT USAGE ON WAREHOUSE NL_ASSISTANT_WH TO ROLE NL_ASSISTANT_READER;
GRANT USAGE ON DATABASE ANALYTICS TO ROLE NL_ASSISTANT_READER;
GRANT USAGE ON SCHEMA ANALYTICS.CLAIMS_APP TO ROLE NL_ASSISTANT_READER;
GRANT SELECT ON VIEW ANALYTICS.CLAIMS_APP.V_PROVIDERS TO ROLE NL_ASSISTANT_READER;
GRANT SELECT ON VIEW ANALYTICS.CLAIMS_APP.V_CLAIMS TO ROLE NL_ASSISTANT_READER;

-- 4. Service user with key-pair auth (no password).
--    Generate keys:  openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
--                    openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
--    Store rsa_key.p8 in Secrets Manager; paste the public key body below.
CREATE USER IF NOT EXISTS SVC_NL_ASSISTANT
  TYPE = SERVICE
  DEFAULT_ROLE = NL_ASSISTANT_READER
  DEFAULT_WAREHOUSE = NL_ASSISTANT_WH
  RSA_PUBLIC_KEY = 'REPLACE_WITH_PUBLIC_KEY_BODY';
GRANT ROLE NL_ASSISTANT_READER TO USER SVC_NL_ASSISTANT;

-- 5. Restrict where the service user can connect from (NAT egress IPs or PrivateLink).
-- CREATE NETWORK POLICY NL_ASSISTANT_NP ALLOWED_IP_LIST = ('203.0.113.10/32');
-- ALTER USER SVC_NL_ASSISTANT SET NETWORK_POLICY = NL_ASSISTANT_NP;

-- 6. Audit: every query carries a JSON QUERY_TAG with the tool name.
-- SELECT query_tag, query_text, total_elapsed_time FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
-- WHERE user_name = 'SVC_NL_ASSISTANT' ORDER BY start_time DESC LIMIT 50;

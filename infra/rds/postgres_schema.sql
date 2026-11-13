-- Fake provider/claims tables for RDS PostgreSQL.
-- Run as the RDS master user (or another user allowed to create schemas and roles):
--   psql "host=$PGHOST dbname=$PGDATABASE user=$PGUSER sslmode=require" -f infra/rds/postgres_schema.sql
-- Then load data with infra/rds/load_postgres.sh.
--
-- Unquoted identifiers fold to lower case in PostgreSQL, so the upper-case names in
-- mcp_server/schema_mapping.py (NPI, PROVIDER_NAME, ...) resolve to these columns.
-- Safe to re-run: drops and recreates the two tables.

CREATE SCHEMA IF NOT EXISTS claims_app;

DROP TABLE IF EXISTS claims_app.claims;
DROP TABLE IF EXISTS claims_app.providers;

CREATE TABLE claims_app.providers (
    npi              CHAR(10)      PRIMARY KEY CHECK (npi ~ '^[12][0-9]{9}$'),
    provider_name    VARCHAR(200)  NOT NULL,
    entity_type      VARCHAR(20)   NOT NULL CHECK (entity_type IN ('Individual', 'Organization')),
    specialty        VARCHAR(100)  NOT NULL,
    taxonomy_code    CHAR(10)      NOT NULL,
    city             VARCHAR(100)  NOT NULL,
    state            CHAR(2)       NOT NULL,
    zip              CHAR(5)       NOT NULL,
    enumeration_date DATE          NOT NULL
);

CREATE TABLE claims_app.claims (
    claim_id        VARCHAR(20)    PRIMARY KEY,
    rendering_npi   CHAR(10)       NOT NULL REFERENCES claims_app.providers (npi),
    billing_npi     CHAR(10)       NOT NULL REFERENCES claims_app.providers (npi),
    member_id       VARCHAR(20)    NOT NULL,
    service_date    DATE           NOT NULL,
    submitted_date  DATE           NOT NULL,
    claim_status    VARCHAR(20)    NOT NULL
        CHECK (claim_status IN ('PAID', 'DENIED', 'PENDING', 'ADJUSTED', 'REJECTED')),
    billed_amount   NUMERIC(12, 2) NOT NULL CHECK (billed_amount >= 0),
    paid_amount     NUMERIC(12, 2) NOT NULL CHECK (paid_amount >= 0 AND paid_amount <= billed_amount),
    CHECK (submitted_date >= service_date)
);

-- Match the MCP tools' filters: per-provider date ranges and status counts by date.
CREATE INDEX claims_rendering_npi_submitted_idx ON claims_app.claims (rendering_npi, submitted_date);
CREATE INDEX claims_billing_npi_submitted_idx   ON claims_app.claims (billing_npi, submitted_date);
CREATE INDEX claims_submitted_status_idx        ON claims_app.claims (submitted_date, claim_status);

-- Read-only role for the application, mirroring infra/snowflake/setup_readonly_role.sql.
-- Create a login user separately and grant it this role, e.g.:
--   CREATE USER nl_assistant_app WITH PASSWORD '<set-in-secrets-manager>';
--   GRANT nl_assistant_reader TO nl_assistant_app;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nl_assistant_reader') THEN
        CREATE ROLE nl_assistant_reader NOLOGIN;
    END IF;
END
$$;
GRANT USAGE ON SCHEMA claims_app TO nl_assistant_reader;
GRANT SELECT ON claims_app.providers, claims_app.claims TO nl_assistant_reader;

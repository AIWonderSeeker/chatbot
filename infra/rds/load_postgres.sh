#!/usr/bin/env bash
# Create the tables and load the fake CSVs into RDS PostgreSQL.
#
# Connection comes from the standard libpq environment variables; nothing is
# hard-coded and no password is passed on the command line:
#   export PGHOST=<your-instance>.<id>.<region>.rds.amazonaws.com
#   export PGPORT=5432 PGDATABASE=<db> PGUSER=<master-or-admin-user>
#   export PGSSLMODE=require
#   # Password: use ~/.pgpass, or an IAM auth token in PGPASSWORD, or let psql prompt,
#   # or set RDS_SECRET_ARN to the RDS-managed master secret and the script reads it
#   # with the AWS CLI (needs jq) without printing it.
#   # Optional: PGSSLMODE=verify-full PGSSLROOTCERT=global-bundle.pem to verify the server.
#
# Usage:
#   python scripts/generate_fake_npi_data.py        # writes data/fake/*.csv
#   infra/rds/load_postgres.sh [data-dir]           # default data/fake
set -euo pipefail

DATA_DIR="${1:-data/fake}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${PGHOST:?Set PGHOST to your RDS endpoint}"
: "${PGDATABASE:?Set PGDATABASE}"
: "${PGUSER:?Set PGUSER}"
export PGSSLMODE="${PGSSLMODE:-require}"

if [[ -z "${PGPASSWORD:-}" && -n "${RDS_SECRET_ARN:-}" ]]; then
  echo "Reading database password from Secrets Manager ..."
  PGPASSWORD="$(aws secretsmanager get-secret-value --secret-id "$RDS_SECRET_ARN" \
    --query SecretString --output text | jq -r '.password')"
  export PGPASSWORD
fi

for f in providers.csv claims.csv; do
  [[ -f "$DATA_DIR/$f" ]] || { echo "Missing $DATA_DIR/$f - run scripts/generate_fake_npi_data.py first" >&2; exit 1; }
done

echo "Creating schema on $PGHOST/$PGDATABASE ..."
psql -v ON_ERROR_STOP=1 -q -f "$SCRIPT_DIR/postgres_schema.sql"

echo "Loading data ..."
psql -v ON_ERROR_STOP=1 -q <<SQL
BEGIN;
\copy claims_app.providers (npi, provider_name, entity_type, specialty, taxonomy_code, city, state, zip, enumeration_date) FROM '$DATA_DIR/providers.csv' WITH (FORMAT csv, HEADER true)
\copy claims_app.claims (claim_id, rendering_npi, billing_npi, member_id, service_date, submitted_date, claim_status, billed_amount, paid_amount) FROM '$DATA_DIR/claims.csv' WITH (FORMAT csv, HEADER true)
COMMIT;
ANALYZE claims_app.providers;
ANALYZE claims_app.claims;
SQL

psql -v ON_ERROR_STOP=1 -At <<'SQL'
SELECT 'providers: ' || count(*) FROM claims_app.providers;
SELECT 'claims:    ' || count(*) FROM claims_app.claims;
SQL
echo "Done."

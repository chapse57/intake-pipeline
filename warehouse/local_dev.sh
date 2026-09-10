#!/usr/bin/env bash
# local_dev.sh — a throwaway PostgreSQL setup for `pytest -m warehouse`.
#
# Needs a reachable PostgreSQL 16 and a superuser connection in $PGSUPER
# (default: the local socket as `postgres`). Creates the database and three
# login roles with obvious passwords, and writes .env.warehouse.local.
# The role grants themselves come from the migrations, not from here.
set -euo pipefail
: "${PGSUPER:=host=/tmp user=postgres}"
: "${DB:=intake_wh}"

psql "$PGSUPER" -v ON_ERROR_STOP=1 -q <<SQL
DROP DATABASE IF EXISTS $DB;
DROP ROLE IF EXISTS wh_admin, loader_test, analyst_test;
CREATE ROLE wh_admin LOGIN PASSWORD 'admin' CREATEROLE;   -- CREATEROLE: 006 creates wh_loader/wh_analyst
CREATE DATABASE $DB OWNER wh_admin;
CREATE ROLE loader_test  LOGIN PASSWORD 'loader';
CREATE ROLE analyst_test LOGIN PASSWORD 'analyst';
SQL

# group membership is granted after the migrations create the groups
hostpart=$(echo "$PGSUPER" | tr ' ' '\n' | grep -E '^(host|port)=' | tr '\n' ' ')
cat > .env.warehouse.local <<ENV
WAREHOUSE_ADMIN_DSN="$hostpart dbname=$DB user=wh_admin password=admin"
WAREHOUSE_LOADER_DSN="$hostpart dbname=$DB user=loader_test password=loader"
WAREHOUSE_ANALYST_DSN="$hostpart dbname=$DB user=analyst_test password=analyst"
ENV
set -a; source .env.warehouse.local; set +a
python -m warehouse.migrate
psql "$PGSUPER dbname=$DB" -v ON_ERROR_STOP=1 -q -c "GRANT wh_loader TO loader_test; GRANT wh_analyst TO analyst_test;"
echo "ready: set -a && source .env.warehouse.local && set +a && pytest -m warehouse -v"

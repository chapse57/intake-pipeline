#!/usr/bin/env bash
# provision.sh — the Azure side, start to finish, as az commands.
#
# Run it in pieces (each step is a function; `provision.sh all` runs them in
# order). Everything it creates fits the free tiers: Flexible Server B1ms is
# 750 h/month free for 12 months on a free or student subscription; Key Vault
# is billed per 10,000 operations and this uses about ten. RUNBOOK.md has
# what happened when it was actually run, including the parts that did not
# go as written here.
#
#   az login --use-device-code          # once, as the subscription owner
#   ./provision.sh all
#
# Nothing here writes a secret to disk. The one secret (the loader service
# principal's client secret) goes straight from `az ad sp` into Key Vault.
set -euo pipefail

: "${RG:=intake-wh-rg}"
: "${LOC:=koreacentral}"
: "${SERVER:=intake-wh-$(az account show --query id -o tsv | cut -c1-8)}"
: "${DB:=intake_wh}"
: "${KV:=intake-wh-kv-$(az account show --query id -o tsv | cut -c1-8)}"
: "${MY_IP:=$(curl -s https://api.ipify.org)}"

me_upn()  { az ad signed-in-user show --query userPrincipalName -o tsv; }
me_oid()  { az ad signed-in-user show --query id -o tsv; }

step_group() {
  az group create -n "$RG" -l "$LOC" -o none
  echo "resource group $RG in $LOC"
}

step_server() {
  # Burstable B1ms, 32 GiB (the minimum), PG 16, 7-day backups (the default),
  # public endpoint with a firewall rule for this machine only. Password auth
  # stays on for the moment: the Entra admin is added in the next step and
  # password auth is switched off only after an Entra login has worked.
  az postgres flexible-server create \
    -g "$RG" -n "$SERVER" -l "$LOC" \
    --tier Burstable --sku-name Standard_B1ms --storage-size 32 --version 16 \
    --backup-retention 7 \
    --active-directory-auth Enabled --password-auth Enabled \
    --admin-user wh_bootstrap --admin-password "$(openssl rand -base64 24)" \
    --public-access "$MY_IP" \
    --database-name "$DB" \
    --yes -o table
  echo "server $SERVER.postgres.database.azure.com  db $DB  firewall: $MY_IP only"
}

step_entra_admin() {
  az postgres flexible-server microsoft-entra-admin create \
    -g "$RG" -s "$SERVER" \
    --display-name "$(me_upn)" --object-id "$(me_oid)" --type User -o none
  echo "Entra admin: $(me_upn)"
  echo "test it:  PGPASSWORD=\$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv) \\"
  echo "          psql \"host=$SERVER.postgres.database.azure.com dbname=$DB user=$(me_upn) sslmode=require\""
}

step_disable_password() {
  # Only after step_entra_admin's psql worked. From here on there is no
  # password anywhere on this server.
  az postgres flexible-server update -g "$RG" -n "$SERVER" --password-auth Disabled -o none
  echo "password authentication disabled; Entra only"
}

step_pgaudit() {
  az postgres flexible-server parameter set -g "$RG" -s "$SERVER" \
    --name shared_preload_libraries --value pgaudit -o none
  az postgres flexible-server parameter set -g "$RG" -s "$SERVER" \
    --name pgaudit.log --value "write,ddl,role" -o none
  az postgres flexible-server restart -g "$RG" -n "$SERVER" -o none
  echo "pgaudit preloaded (write,ddl,role); server restarted"
  echo "then, once, in psql as the Entra admin:  CREATE EXTENSION pgaudit;"
}

step_keyvault() {
  az keyvault create -g "$RG" -n "$KV" -l "$LOC" --enable-rbac-authorization true -o none
  az role assignment create --assignee "$(me_oid)" --role "Key Vault Secrets Officer" \
    --scope "$(az keyvault show -g "$RG" -n "$KV" --query id -o tsv)" -o none
  echo "key vault https://$KV.vault.azure.net (RBAC; you are Secrets Officer)"
}

step_principals() {
  # Two service principals so the tests can be two different identities.
  # Their client secrets go to Key Vault and are never printed.
  for who in loader analyst; do
    local name="intake-wh-$who"
    local json
    json=$(az ad sp create-for-rbac --name "$name" --skip-assignment -o json 2>/dev/null \
        || az ad sp create-for-rbac --name "$name" -o json)
    local app_id secret
    app_id=$(echo "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["appId"])')
    secret=$(echo "$json"  | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
    az keyvault secret set --vault-name "$KV" -n "$name-client-secret" --value "$secret" -o none
    unset secret
    local oid
    oid=$(az ad sp show --id "$app_id" --query id -o tsv)
    echo "$who: client_id=$app_id object_id=$oid secret -> https://$KV.vault.azure.net/secrets/$name-client-secret"
    # the analyst principal may read its own secret; the loader principal too
    az role assignment create --assignee-object-id "$oid" --assignee-principal-type ServicePrincipal \
      --role "Key Vault Secrets User" \
      --scope "$(az keyvault show -g "$RG" -n "$KV" --query id -o tsv)" -o none
  done
  cat <<EOF

then, once, in psql as the Entra admin (after python -m warehouse.migrate):
  SELECT * FROM pgaadauth_create_principal_with_oid('intake-wh-loader',  '<loader object_id>',  'service', false, false);
  SELECT * FROM pgaadauth_create_principal_with_oid('intake-wh-analyst', '<analyst object_id>', 'service', false, false);
  GRANT wh_loader  TO "intake-wh-loader";
  GRANT wh_analyst TO "intake-wh-analyst";
EOF
}

step_env() {
  local host="$SERVER.postgres.database.azure.com"
  cat <<EOF
# put in .env (no secrets in here)
WAREHOUSE_AUTH=entra
AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)
WAREHOUSE_ADMIN_DSN="host=$host dbname=$DB user=$(me_upn) sslmode=require"
WAREHOUSE_LOADER_DSN="host=$host dbname=$DB user=intake-wh-loader sslmode=require"
WAREHOUSE_LOADER_CLIENT_ID=$(az ad sp list --display-name intake-wh-loader --query '[0].appId' -o tsv)
WAREHOUSE_LOADER_SECRET_ID=https://$KV.vault.azure.net/secrets/intake-wh-loader-client-secret
WAREHOUSE_ANALYST_DSN="host=$host dbname=$DB user=intake-wh-analyst sslmode=require"
WAREHOUSE_ANALYST_CLIENT_ID=$(az ad sp list --display-name intake-wh-analyst --query '[0].appId' -o tsv)
WAREHOUSE_ANALYST_SECRET_ID=https://$KV.vault.azure.net/secrets/intake-wh-analyst-client-secret
EOF
}

step_cost() {
  # What the month has actually cost so far, from the billing API.
  az consumption usage list --start-date "$(date -u +%Y-%m-01)" --end-date "$(date -u +%Y-%m-%d)" \
    --query "[?contains(instanceName, 'intake-wh')].{name:instanceName, cost:pretaxCost, currency:currency}" -o table \
    || az costmanagement query --type ActualCost --timeframe MonthToDate \
         --scope "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RG" \
         --dataset-aggregation '{"totalCost":{"name":"PreTaxCost","function":"Sum"}}' -o table
}

step_destroy() {
  az group delete -n "$RG" --yes --no-wait
  az ad sp delete --id "$(az ad sp list --display-name intake-wh-loader  --query '[0].id' -o tsv)" || true
  az ad sp delete --id "$(az ad sp list --display-name intake-wh-analyst --query '[0].id' -o tsv)" || true
  echo "deleting $RG in the background; service principals removed"
}

STEPS="group server entra_admin disable_password pgaudit keyvault principals env cost destroy"
[ $# -gt 0 ] || { echo "usage: $0 all | {$STEPS} ..."; exit 2; }
for s in "$@"; do
  case "$s" in
    all)
      step_group; step_server; step_entra_admin
      echo; echo ">> verify the Entra psql login above, then run: $0 disable_password pgaudit keyvault principals env" ;;
    *)
      case " $STEPS " in *" $s "*) "step_$s" ;; *) echo "unknown step: $s"; exit 2 ;; esac ;;
  esac
done

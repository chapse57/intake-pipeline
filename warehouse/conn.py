"""
One place that knows how to open a connection as one of the three identities.

    connect("admin")    the migration owner    WAREHOUSE_ADMIN_DSN
    connect("loader")   member of wh_loader    WAREHOUSE_LOADER_DSN
    connect("analyst")  member of wh_analyst   WAREHOUSE_ANALYST_DSN

A DSN is an ordinary libpq string. Two ways to authenticate:

  password (default)     the DSN carries the password. Local Postgres, CI.
  WAREHOUSE_AUTH=entra   the DSN carries no password. A Microsoft Entra
                         access token is fetched and used as the password,
                         which is how Azure Database for PostgreSQL works
                         with password authentication switched off.

Under `entra`, the token comes from azure-identity's DefaultAzureCredential
(your `az login` on a workstation, a managed identity on Azure) unless a
per-role service principal is configured:

    WAREHOUSE_<ROLE>_CLIENT_ID
    WAREHOUSE_<ROLE>_CLIENT_SECRET      or
    WAREHOUSE_<ROLE>_SECRET_ID          a Key Vault secret identifier,
                                        https://<vault>.vault.azure.net/secrets/<name>
    AZURE_TENANT_ID

The Key Vault path is the one the loader uses in the recorded Azure run: the
secret is read at connect time and never lands in a file or an env var.
"""
import os

import psycopg

ENTRA_PG_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def _env(role, key, default=None):
    return os.environ.get(f"WAREHOUSE_{role.upper()}_{key}", default)


def _entra_token(role):
    from azure.identity import ClientSecretCredential, DefaultAzureCredential

    client_id = _env(role, "CLIENT_ID")
    if client_id:
        secret = _env(role, "CLIENT_SECRET")
        if not secret:
            secret = _keyvault_secret(_env(role, "SECRET_ID"))
        cred = ClientSecretCredential(os.environ["AZURE_TENANT_ID"], client_id, secret)
    else:
        cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return cred.get_token(ENTRA_PG_SCOPE).token


def _keyvault_secret(secret_id):
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    if not secret_id:
        raise RuntimeError("entra auth with a client id needs CLIENT_SECRET or SECRET_ID")
    vault_url, _, rest = secret_id.partition("/secrets/")
    name = rest.split("/")[0]
    return SecretClient(vault_url, DefaultAzureCredential()).get_secret(name).value


def dsn_for(role):
    dsn = _env(role, "DSN")
    if not dsn:
        raise RuntimeError(f"WAREHOUSE_{role.upper()}_DSN is not set")
    return dsn


def connect(role_or_dsn, **kw):
    """Open a psycopg connection. Autocommit off; callers own transactions."""
    if "=" in role_or_dsn or "://" in role_or_dsn:
        dsn, role = role_or_dsn, None
    else:
        dsn, role = dsn_for(role_or_dsn), role_or_dsn

    if os.environ.get("WAREHOUSE_AUTH", "password").lower() == "entra":
        if role is None:
            raise RuntimeError("entra auth needs a role name, not a bare DSN")
        if "sslmode=" not in dsn and "ssl=" not in dsn:
            dsn += " sslmode=require"
        kw["password"] = _entra_token(role)

    return psycopg.connect(dsn, **kw)

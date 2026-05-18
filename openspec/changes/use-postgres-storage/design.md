## Context

The sidecar currently stores provisioning flow state, runtime settings, operational samples, notification state, rotation pools, assignment state, and credit-control data through a SQLite-backed implementation. The user requested a PostgreSQL-only implementation with a Postgres Alpine container in Docker Compose, password in `.env`, and no data migration.

## Goals / Non-Goals

**Goals:**
- Make PostgreSQL the only runtime store.
- Require structured PostgreSQL connection settings in `config.yaml`, read `POSTGRES_PASSWORD` from `.env`, assemble the connection string inside the application, and reject old SQLite or direct connection-string configuration.
- Initialize schema automatically in an empty PostgreSQL database.
- Run app and store tests against PostgreSQL.
- Add Docker Compose PostgreSQL service using `.env` password and `config.yaml` database url/port/username/name.

**Non-Goals:**
- No migration from existing SQLite files.
- No compatibility fallback to SQLite.
- No multi-database abstraction selector.

## Decisions

- Use `psycopg[binary]` and a synchronous connection-per-operation store to match the current FastAPI service style and minimize service-layer churn.
- Keep the current JSON payload storage pattern so existing model validation remains the source of truth and schema creation stays compact.
- Store timestamps as text in the existing ISO format to preserve ordering semantics already used by the code.
- Use `BIGSERIAL` for operational sample/snapshot ids because PostgreSQL does not support SQLite `AUTOINCREMENT`.
- Reject `storage.*`, `SQLITE_DB_PATH`, `DATABASE_URL`, `POSTGRES_DB`, and `POSTGRES_USER` during config load so deployments fail loudly instead of silently running an old path or bypassing the structured database config.
- Use a named Docker volume for PostgreSQL data and no sidecar data bind mount.

## Risks / Trade-offs

- Existing SQLite data is not migrated → deployment starts with empty PostgreSQL state as requested.
- Tests require PostgreSQL → CI and local test setup must start the Postgres service.
- Connection-per-operation is simple but not pooled → acceptable for current sidecar scale; pooling can be added later if needed.

## Migration Plan

1. Set `database.url`, `database.port`, `database.username`, and `database.name` in `config.yaml`.
2. Set `POSTGRES_PASSWORD` in `.env`.
3. Start the compose stack so PostgreSQL becomes healthy before the sidecar starts.
4. The sidecar creates PostgreSQL tables automatically on startup.
5. Do not copy or import SQLite files.

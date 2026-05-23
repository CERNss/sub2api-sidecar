## Why

SQLite file persistence has become a deployment risk for scheduler, alerting, orchestration, and credit-control state. The sidecar needs one shared PostgreSQL-backed persistence layer so local state is durable under container restarts and consistent across the background jobs that read the collected operational data.

## What Changes

- **BREAKING** Replace SQLite storage with PostgreSQL-only runtime persistence.
- **BREAKING** Require structured PostgreSQL connection settings in `config.yaml` plus `POSTGRES_PASSWORD` in `.env`; the application assembles the connection string internally and rejects old SQLite and direct database-URL configuration.
- Add a PostgreSQL store that initializes the required schema in an empty PostgreSQL database.
- Update Docker Compose to run `postgres:17-alpine`, keep its password in `.env`, and persist data in a named volume.
- Update tests and CI to run against PostgreSQL instead of SQLite.
- Do not migrate any existing SQLite data.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `deployment-tooling`: Compose runtime now includes PostgreSQL Alpine and persists PostgreSQL data.
- `openai-oauth-provisioning`: Flow persistence now uses PostgreSQL and rejects SQLite configuration.
- `orchestration-dashboard`: Flow inspection reads PostgreSQL-backed records.
- `group-rotation`: Assignment and rotation audit state survive restarts through PostgreSQL.

## Impact

- Affected code: `app/config.py`, `app/main.py`, service store type hints, `app/stores/postgres.py`, tests, CI, docs, and Docker Compose.
- Affected dependency: adds `psycopg[binary]`.
- Affected deployment: operators must provide `database.url`, `database.port`, `database.username`, and `database.name` in `config.yaml`, `POSTGRES_PASSWORD` in `.env`, and an available PostgreSQL database. Fresh deployments start with an empty PostgreSQL schema.

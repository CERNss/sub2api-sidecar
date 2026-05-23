## 1. Specification

- [x] 1.1 Record the PostgreSQL-only storage change and no-migration decision.
- [x] 1.2 Update affected capability specs for PostgreSQL persistence and deployment.

## 2. Runtime Implementation

- [x] 2.1 Add a PostgreSQL store implementation and remove the SQLite store.
- [x] 2.2 Require structured PostgreSQL config plus `POSTGRES_PASSWORD` and reject old SQLite/direct-URL configuration.
- [x] 2.3 Update services and app wiring to use `PostgresFlowStore`.

## 3. Deployment

- [x] 3.1 Add `psycopg[binary]` to runtime dependencies.
- [x] 3.2 Add `postgres:17-alpine` to Docker Compose with `.env` password and `npm-network`.
- [x] 3.3 Update `.env.example`, `config.example.yaml`, README, and CI.

## 4. Verification

- [x] 4.1 Run Python compile checks.
- [x] 4.2 Run the backend test suite against PostgreSQL.
- [x] 4.3 Run OpenSpec validation.

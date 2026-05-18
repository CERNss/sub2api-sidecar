## MODIFIED Requirements

### Requirement: Provide local compose runtime assets
The project SHALL include Docker Compose configuration for running the service locally in a container.

#### Scenario: Compose uses project configuration and persists PostgreSQL data
- **GIVEN** the operator has prepared project `.env` and `config.yaml` files
- **WHEN** the operator runs `docker compose up`
- **THEN** the compose configuration reads secrets from `.env`
- **THEN** the compose configuration mounts `config.yaml` into the container
- **THEN** host port `8000` maps to container port `8000`
- **THEN** the compose configuration starts a PostgreSQL Alpine container
- **THEN** the sidecar reads PostgreSQL url, port, username, and database name from `config.yaml`
- **THEN** the sidecar reads `POSTGRES_PASSWORD` from `.env`
- **THEN** the sidecar assembles its PostgreSQL connection string internally
- **THEN** PostgreSQL data persists through a named Docker volume

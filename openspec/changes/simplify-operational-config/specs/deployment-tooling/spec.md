## MODIFIED Requirements

### Requirement: Provide local compose runtime assets
The project SHALL include Docker Compose configuration for running the service locally in a container.

#### Scenario: Compose uses project configuration and persists SQLite data
- **GIVEN** the operator has prepared project `.env` and `config.yaml` files
- **WHEN** the operator runs `docker compose up`
- **THEN** the compose configuration reads secrets from `.env`
- **THEN** the compose configuration mounts `config.yaml` into the container
- **THEN** host port `8000` maps to container port `8000`
- **THEN** the SQLite data directory persists through a mounted project `data/` directory
- **THEN** deployment config contains no `auto_rotation`, `credit_control`, or `operational_data` runtime sections
- **THEN** deployment config contains no `provisioning.assignment_mode` runtime setting
- **THEN** removed runtime scheduler, switch, expiration, or policy fields in deployment config prevent startup instead of being ignored

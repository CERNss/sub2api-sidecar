# deployment-tooling Specification

## Purpose
Define the required container build and local runtime tooling for packaging and running the Sub2API sidecar service.

## Requirements

### Requirement: Provide container image build assets
The project SHALL include container build assets that package the sidecar into a runnable container image.

#### Scenario: Dockerfile packages the runtime service
- **GIVEN** the repository is available on a build host
- **WHEN** an operator builds the image from the project Dockerfile
- **THEN** the image installs the runtime dependencies
- **THEN** the image includes the application code needed to start the service
- **THEN** the image starts the FastAPI service on port `8000`

### Requirement: Provide a repeatable Linux x64 build script
The project SHALL include a build helper that uses Docker Buildx to build a `linux/amd64` image from the repository.

#### Scenario: Default build targets Linux x64
- **GIVEN** Docker and Docker Buildx are available
- **WHEN** the operator runs `./build.sh` from the project root
- **THEN** the script targets `linux/amd64`
- **THEN** the script builds from the repository Dockerfile and build context
- **THEN** the script tags the image with a default configurable name and tag

#### Scenario: Operator overrides image metadata or output mode
- **GIVEN** Docker and Docker Buildx are available
- **WHEN** the operator passes supported flags or environment variables to `build.sh`
- **THEN** the script can override image name, tag, platform, builder name, Dockerfile path, and build context
- **THEN** the script can choose between loading the image locally and pushing it to a registry

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

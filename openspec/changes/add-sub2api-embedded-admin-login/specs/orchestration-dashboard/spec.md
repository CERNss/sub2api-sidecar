# orchestration-dashboard Specification

## MODIFIED Requirements

### Requirement: React dashboard renders orchestration state
The React UI SHALL provide an authenticated orchestration workspace for moving existing users or keys between groups, browsing provisioning flows, configuring webhook alert routing for operational signals, and supporting embedded launch from the Sub2API admin dashboard.

#### Scenario: Embedded admin opens the sidecar with a Sub2API token
- **GIVEN** an unauthenticated browser opens the sidecar from `/admin/sidecar` with a `token` query parameter
- **WHEN** the React app starts
- **THEN** it SHALL exchange the token through `POST /auth/sub2api-login`
- **AND** it SHALL show an in-progress admin login state while the exchange is pending
- **AND** it SHALL remove the `token` query parameter from the URL after success or failure
- **AND** on success it SHALL render the normal operator dashboard using the sidecar session cookie
- **AND** on failure it SHALL navigate to the sidecar login page

#### Scenario: Prefixed embedded routes preserve logical dashboard routing
- **GIVEN** the sidecar is loaded from `/admin/sidecar`
- **WHEN** the operator navigates between dashboard views
- **THEN** browser URLs SHALL remain under `/admin/sidecar`
- **AND** the app's logical route handling SHALL continue to treat `/orchestration`, `/provision`, `/dashboard`, and `/notifications` as the active app routes
- **AND** API requests SHALL be sent under the `/admin/sidecar` prefix so same-site reverse proxy deployments can route them to the sidecar

#### Scenario: Unauthenticated embedded route redirects to prefixed login
- **GIVEN** a browser opens `/admin/sidecar/orchestration/manual` without a sidecar session and without a token query parameter
- **WHEN** the sidecar handles the request
- **THEN** it SHALL redirect to `/admin/sidecar/login`
- **AND** the redirect SHALL preserve the requested logical next path

#### Scenario: Embedded React assets are served from the sidecar prefix
- **GIVEN** the sidecar serves the React shell from `/admin/sidecar`
- **WHEN** the HTML is returned
- **THEN** static asset URLs SHALL point under `/admin/sidecar/ui-static`
- **AND** the embedded dashboard SHALL load without requiring a separate Vite build base

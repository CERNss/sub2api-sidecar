## 1. Backend Token Exchange

- [x] 1.1 Add a request schema for Sub2API token login.
- [x] 1.2 Validate Sub2API browser JWTs through `/api/v1/auth/me` without the admin API key.
- [x] 1.3 Reject missing, invalid, or non-admin tokens without issuing a cookie.
- [x] 1.4 Create normal sidecar sessions from verified admin profiles.

## 2. Frontend Startup Flow

- [x] 2.1 Detect incoming `token` query parameters.
- [x] 2.2 Exchange the token for a sidecar session.
- [x] 2.3 Remove the token from the browser URL after success or failure.

## 3. Verification

- [x] 3.1 Add backend tests for admin-token exchange and non-admin rejection.
- [x] 3.2 Run the sidecar test suite and frontend build.

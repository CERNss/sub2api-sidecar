## 1. Spec And Auth Foundation

- [x] 1.1 Add OpenSpec proposal, design, and delta spec files for ephemeral admin auth
- [x] 1.2 Extend environment-backed settings for auth username, startup password override, and access-key TTL
- [x] 1.3 Implement an in-memory auth manager that generates/logs startup credentials and mints expiring access keys

## 2. API And UI Integration

- [x] 2.1 Add login/logout endpoints and protect the operator page plus provisioning APIs
- [x] 2.2 Create a dedicated login page with startup-password guidance and keep the provisioning page focused on the manual localhost paste-back flow
- [x] 2.3 Preserve cookie-based browser access and header-based API access using the same issued access key

## 3. Verification And Documentation

- [x] 3.1 Add or update tests for redirect-to-login, login success/failure, protected APIs, and authenticated provisioning completion after restart-like cache resets
- [x] 3.2 Update `.env.example`, `README.md`, and the main OpenSpec spec so startup, login, and curl usage are documented
- [x] 3.3 Run the test suite and validate the OpenSpec change

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

ACCESS_KEY_COOKIE_NAME = "sub2api_access_key"


@dataclass(frozen=True)
class AuthSession:
    access_key: str
    username: str
    created_at: datetime
    expires_at: datetime | None
    purpose: str


class EphemeralAdminAuthManager:
    """Small in-memory auth manager for local operator access."""

    def __init__(
        self,
        username: str,
        password: str | None,
        access_key_ttl_hours: int,
    ) -> None:
        if access_key_ttl_hours <= 0:
            raise ValueError("access_key_ttl_hours must be greater than zero")

        self.username = username
        self.access_key_ttl = timedelta(hours=access_key_ttl_hours)
        self.password = password or secrets.token_urlsafe(18)
        self.password_source = "env_override" if password else "generated"
        self._sessions: dict[str, AuthSession] = {}

        self._log_startup_credentials()

    @property
    def cookie_max_age_seconds(self) -> int:
        return int(self.access_key_ttl.total_seconds())

    def login(self, username: str, password: str) -> AuthSession | None:
        self._purge_expired_sessions()
        if not self._credentials_match(username=username, password=password):
            logger.warning("Admin login rejected | username=%s", username)
            return None

        return self.create_external_session(username=self.username)

    def create_external_session(self, username: str) -> AuthSession:
        return self._create_session(
            username=username,
            purpose="external",
            expires_in=self.access_key_ttl,
        )

    def create_api_token(self, username: str) -> AuthSession:
        revoked_count = self.revoke_api_tokens(username=username)
        session = self._create_session(username=username, purpose="api_token", expires_in=None)
        logger.info(
            "Admin API token rotated | username=%s | revoked_count=%s",
            username,
            revoked_count,
        )
        return session

    def revoke_api_tokens(self, username: str) -> int:
        token_keys = [
            access_key
            for access_key, session in self._sessions.items()
            if session.username == username and session.purpose == "api_token"
        ]
        for access_key in token_keys:
            self._sessions.pop(access_key, None)
        return len(token_keys)

    def _create_session(
        self,
        *,
        username: str,
        purpose: str,
        expires_in: timedelta | None,
    ) -> AuthSession:
        self._purge_expired_sessions()
        now = datetime.now(timezone.utc)
        expires_at = now + expires_in if expires_in is not None else None
        session = AuthSession(
            access_key=secrets.token_urlsafe(32),
            username=username,
            created_at=now,
            expires_at=expires_at,
            purpose=purpose,
        )
        self._sessions[session.access_key] = session
        logger.info(
            "Admin session issued | username=%s | purpose=%s | expires_at=%s",
            session.username,
            purpose,
            session.expires_at.isoformat() if session.expires_at is not None else "never",
        )
        return session

    def get_session(self, access_key: str | None) -> AuthSession | None:
        if not access_key:
            return None

        self._purge_expired_sessions()
        session = self._sessions.get(access_key)
        if not session:
            return None

        if session.expires_at is not None and session.expires_at <= datetime.now(timezone.utc):
            self._sessions.pop(access_key, None)
            return None
        return session

    def revoke(self, access_key: str | None) -> None:
        if not access_key:
            return
        self._sessions.pop(access_key, None)

    def _credentials_match(self, username: str, password: str) -> bool:
        return secrets.compare_digest(username, self.username) and secrets.compare_digest(
            password, self.password
        )

    def _purge_expired_sessions(self) -> None:
        now = datetime.now(timezone.utc)
        expired_keys = [
            access_key
            for access_key, session in self._sessions.items()
            if session.expires_at is not None and session.expires_at <= now
        ]
        for access_key in expired_keys:
            self._sessions.pop(access_key, None)

    def _log_startup_credentials(self) -> None:
        if self.password_source == "generated":
            logger.warning(
                "Ephemeral admin credentials ready | username=%s | password=%s | "
                "note=Copy this password from startup logs. It changes on every restart.",
                self.username,
                self.password,
            )
            return

        logger.warning(
            "Ephemeral admin credentials ready | username=%s | "
            "password_source=APP_AUTH_PASSWORD | note=Using auth password override from environment.",
            self.username,
        )

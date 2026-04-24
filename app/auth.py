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
    expires_at: datetime


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

        now = datetime.now(timezone.utc)
        session = AuthSession(
            access_key=secrets.token_urlsafe(32),
            username=self.username,
            created_at=now,
            expires_at=now + self.access_key_ttl,
        )
        self._sessions[session.access_key] = session
        logger.info(
            "Admin login succeeded | username=%s | expires_at=%s",
            session.username,
            session.expires_at.isoformat(),
        )
        return session

    def get_session(self, access_key: str | None) -> AuthSession | None:
        if not access_key:
            return None

        self._purge_expired_sessions()
        session = self._sessions.get(access_key)
        if not session:
            return None

        if session.expires_at <= datetime.now(timezone.utc):
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
            if session.expires_at <= now
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

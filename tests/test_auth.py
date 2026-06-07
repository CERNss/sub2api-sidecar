from __future__ import annotations

from datetime import datetime, timezone

from app.auth import EphemeralAdminAuthManager
from app.models.auth import PersistedAuthSession


class FakeAuthSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, PersistedAuthSession] = {}

    def save_auth_session(self, session: PersistedAuthSession) -> PersistedAuthSession:
        self.sessions[session.access_key_hash] = session
        return session

    def get_auth_session(self, access_key_hash: str) -> PersistedAuthSession | None:
        session = self.sessions.get(access_key_hash)
        if session is None or session.revoked_at is not None:
            return None
        return session

    def revoke_auth_session(self, access_key_hash: str) -> None:
        session = self.sessions.get(access_key_hash)
        if session is None or session.revoked_at is not None:
            return
        session.revoked_at = datetime.now(timezone.utc)
        session.updated_at = session.revoked_at

    def revoke_auth_sessions(self, *, username: str, purpose: str) -> int:
        revoked_count = 0
        for session in self.sessions.values():
            if (
                session.username == username
                and session.purpose == purpose
                and session.revoked_at is None
            ):
                session.revoked_at = datetime.now(timezone.utc)
                session.updated_at = session.revoked_at
                revoked_count += 1
        return revoked_count


def test_api_token_persists_and_rotates_across_auth_manager_instances() -> None:
    store = FakeAuthSessionStore()
    first_manager = EphemeralAdminAuthManager(
        username="admin",
        password="secret",
        access_key_ttl_hours=12,
        session_store=store,
    )
    api_token = first_manager.create_api_token("admin")
    browser_session = first_manager.login("admin", "secret")

    restarted_manager = EphemeralAdminAuthManager(
        username="admin",
        password="secret",
        access_key_ttl_hours=12,
        session_store=store,
    )

    persisted_session = restarted_manager.get_session(api_token.access_key)
    missing_browser_session = restarted_manager.get_session(
        browser_session.access_key if browser_session else None
    )

    assert persisted_session is not None
    assert persisted_session.username == "admin"
    assert persisted_session.expires_at is None
    assert missing_browser_session is None

    rotated_token = restarted_manager.create_api_token("admin")

    assert restarted_manager.get_session(api_token.access_key) is None
    assert restarted_manager.get_session(rotated_token.access_key) is not None

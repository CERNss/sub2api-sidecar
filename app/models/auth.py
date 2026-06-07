from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class PersistedAuthSession(BaseModel):
    access_key_hash: str
    username: str
    purpose: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

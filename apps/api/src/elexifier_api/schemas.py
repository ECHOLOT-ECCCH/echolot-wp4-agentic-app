"""API response and request models shared by routers."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    created_at: datetime
    last_login_at: datetime | None = None


class HealthOut(BaseModel):
    status: str
    database: str
    events: str

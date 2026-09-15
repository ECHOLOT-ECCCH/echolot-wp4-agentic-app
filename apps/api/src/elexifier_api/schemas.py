"""API response and request models shared by routers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    source_type: Literal["xml", "pdf"]


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    default_model: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class ProjectOut(ApiModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str | None
    source_type: str
    default_model: dict[str, Any] | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    my_role: str


class MemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["editor", "viewer"] = "editor"


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: str


class FileOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    name: str
    size: int
    sha256: str
    mime: str
    meta: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: datetime

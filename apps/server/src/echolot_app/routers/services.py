"""The services scripts can call, as the agent and the UI know them (docs/PLAN.md 7.7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from echolot_app.deps import CurrentSettings, get_current_user
from echolot_app.service_docs import load_service_docs

router = APIRouter(
    prefix="/api/services", tags=["services"], dependencies=[Depends(get_current_user)]
)


class ServiceOut(BaseModel):
    name: str
    description: str


@router.get("", response_model=list[ServiceOut])
async def list_services(settings: CurrentSettings) -> list[ServiceOut]:
    docs = load_service_docs(settings.services_dir)
    return [ServiceOut(name=d.name, description=d.description) for d in docs.values()]

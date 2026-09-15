"""Per-process application state created in the lifespan and read through dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from elexifier_agent.catalog import ModelCatalog
from elexifier_api.auth.oidc import OidcClient
from elexifier_api.auth.session import SessionStore
from elexifier_core.crypto import SecretBox
from elexifier_core.db.session import Database
from elexifier_core.events import EventBus
from elexifier_core.settings import Settings
from elexifier_core.storage import Storage


@dataclass
class AppState:
    settings: Settings
    db: Database
    storage: Storage
    secret_box: SecretBox
    session_store: SessionStore
    oidc: OidcClient
    event_bus: EventBus
    catalog: ModelCatalog

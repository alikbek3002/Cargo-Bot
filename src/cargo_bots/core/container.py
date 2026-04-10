from __future__ import annotations

from dataclasses import dataclass

from cargo_bots.bots.runtime import BotRuntime
from cargo_bots.core.config import Settings
from cargo_bots.db.session import Database
from cargo_bots.services.client_service import ClientService
from cargo_bots.services.import_service import ImportService
from cargo_bots.services.notification_service import NotificationService


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    database: Database
    runtime: BotRuntime
    client_service: ClientService
    import_service: ImportService
    notification_service: NotificationService


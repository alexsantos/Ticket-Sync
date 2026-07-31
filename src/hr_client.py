from __future__ import annotations

from src.config import Settings
from src.osticket_client import OsTicketClient


def build_hr_client(settings: Settings) -> OsTicketClient:
    return OsTicketClient(settings.hr_api_base_url, settings.hr_api_key, settings.http_timeout)

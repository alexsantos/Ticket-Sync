from __future__ import annotations

from src.config import Settings
from src.osticket_client import OsTicketClient


def build_ops_client(settings: Settings) -> OsTicketClient:
    return OsTicketClient(settings.ops_api_base_url, settings.ops_api_key, settings.http_timeout)

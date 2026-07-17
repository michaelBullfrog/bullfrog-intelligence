from typing import Any
from .base import Connector
from ..config import settings

class CcwrConnector(Connector):
    name = "ccwr"

    async def health(self) -> dict[str, Any]:
        return {
            "system": self.name,
            "configured": bool(
                settings.ccwr_base_url
                and settings.ccwr_client_id
                and settings.ccwr_client_secret
            ),
        }

    async def get_expiring_subscriptions(self, days: int = 90) -> list[dict[str, Any]]:
        return [{
            "mode": "demo",
            "days": days,
            "message": "Add the existing Bullfrog CCW-R renewal pull here.",
        }]

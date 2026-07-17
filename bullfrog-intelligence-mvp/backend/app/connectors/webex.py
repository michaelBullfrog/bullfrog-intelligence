from typing import Any
from .base import Connector
from ..config import settings

class WebexConnector(Connector):
    name = "webex"

    async def health(self) -> dict[str, Any]:
        return {
            "system": self.name,
            "configured": bool(settings.webex_access_token),
            "org_id_configured": bool(settings.webex_org_id),
        }

    async def get_contact_center_summary(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        customer_name: str | None = None,
    ) -> dict[str, Any]:
        return {
            "mode": "demo" if not settings.webex_access_token else "configured",
            "customer": customer_name,
            "start_date": start_date,
            "end_date": end_date,
            "calls_offered": 0,
            "calls_handled": 0,
            "calls_abandoned": 0,
            "message": "Add the Bullfrog Webex reporting query in this connector.",
        }

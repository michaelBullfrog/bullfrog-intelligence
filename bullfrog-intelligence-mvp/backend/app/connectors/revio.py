from typing import Any
import httpx
from .base import Connector
from ..config import settings

class RevioConnector(Connector):
    name = "revio"

    async def health(self) -> dict[str, Any]:
        configured = bool(settings.revio_base_url and settings.revio_api_key)
        return {"system": self.name, "configured": configured}

    async def search_tickets(
        self,
        *,
        status: str | None = None,
        customer_name: str | None = None,
        max_results: int = 25,
    ) -> list[dict[str, Any]]:
        if not settings.revio_base_url or not settings.revio_api_key:
            return [{
                "ticket_id": "DEMO-1001",
                "customer": customer_name or "Demo Customer",
                "status": status or "Open",
                "subject": "Rev.io connector is running in demo mode",
            }]

        headers = {
            "Authorization": f"Bearer {settings.revio_api_key}",
            "Accept": "application/json",
        }
        params = {"perPage": max_results}
        if status:
            params["status"] = status

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{settings.revio_base_url.rstrip('/')}/psac/api/v1/ticket-list",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            payload = response.json()

        tickets = payload.get("data", payload if isinstance(payload, list) else [])
        if customer_name:
            needle = customer_name.lower()
            tickets = [
                t for t in tickets
                if needle in str(t.get("customerName", t.get("customer", ""))).lower()
            ]
        return tickets[:max_results]

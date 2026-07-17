from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import Connector
from ..config import settings

class RevioConnector(Connector):
    name = "revio"

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_exp: int | None = None
        self._token_lock = asyncio.Lock()

    def _decode_exp(self, token: str) -> int | None:
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
            return int(data["exp"]) if data.get("exp") else None
        except Exception:
            return None

    def _token_valid(self) -> bool:
        if not self._token:
            return False
        if self._token_exp is None:
            return True
        return time.time() < self._token_exp - settings.revio_token_refresh_buffer_seconds

    async def _exchange_token(self, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token_valid():
                return self._token or ""

            async with httpx.AsyncClient(timeout=settings.revio_request_timeout_seconds, verify=settings.revio_verify_ssl) as client:
                response = await client.post(
                    settings.revio_url(settings.revio_token_exchange_path),
                    json={"apiKey": settings.revio_api_key},
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()

            token = payload.get("data", {}).get("token")
            if not token:
                raise RuntimeError(f"No data.token in token response: {str(payload)[:500]}")

            self._token = str(token)
            self._token_exp = self._decode_exp(self._token)
            return self._token

    async def _request_tickets(self, *, page: int, page_size: int, force_token_refresh: bool = False) -> httpx.Response:
        token = await self._exchange_token(force=force_token_refresh)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Revio-Host": settings.revio_host,
            "Accept": "application/json",
        }
        params = {"page": page, "perPage": page_size}
        async with httpx.AsyncClient(timeout=settings.revio_request_timeout_seconds, verify=settings.revio_verify_ssl) as client:
            return await client.get(settings.revio_url(settings.revio_ticket_list_path), headers=headers, params=params)

    def _parse_date(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    def _normalize(self, row: dict[str, Any]) -> dict[str, Any]:
        created = self._parse_date(row.get("createdDate"))
        opened = self._parse_date(row.get("openedDate"))
        age_start = opened or created
        age_days = max((datetime.now(timezone.utc) - age_start).days, 0) if age_start else None
        return {
            "ticket_id": str(row.get("ticketId", "unknown")),
            "subject": row.get("ticketDescription") or "Untitled ticket",
            "customer_id": row.get("customerId"),
            "customer_name": row.get("customerName") or "Unknown customer",
            "status": row.get("ticketStatus") or row.get("ticketState") or "Unknown",
            "priority": row.get("ticketPriority"),
            "severity": row.get("ticketSeverity"),
            "ticket_type": row.get("ticketType"),
            "assigned_engineer": row.get("techAssigned") or None,
            "assigned_engineer_id": row.get("techAssignedId"),
            "created_at": row.get("createdDate"),
            "modified_at": row.get("modifiedDate"),
            "opened_at": row.get("openedDate"),
            "closed_at": row.get("closedDate"),
            "age_days": age_days,
        }

    async def health(self) -> dict[str, Any]:
        if not settings.revio_configured:
            return {"system": self.name, "configured": False, "reachable": False}
        try:
            token = await self._exchange_token()
            return {"system": self.name, "configured": True, "reachable": bool(token), "mode": "live"}
        except Exception as exc:
            return {"system": self.name, "configured": True, "reachable": False, "error": str(exc)}

    async def search_tickets(self, *, status: str | None = None, customer_name: str | None = None, assigned_engineer: str | None = None, minimum_age_days: int | None = None, page: int = 1, page_size: int = 100, max_results: int | None = None) -> list[dict[str, Any]]:
        if max_results is not None:
            page_size = max_results

        response = await self._request_tickets(page=page, page_size=min(max(page_size, 1), 500))
        if response.status_code == 401:
            response = await self._request_tickets(page=page, page_size=min(max(page_size, 1), 500), force_token_refresh=True)
        response.raise_for_status()

        payload = response.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        tickets = [self._normalize(row) for row in rows if isinstance(row, dict)]

        if status:
            needle = status.casefold()
            tickets = [t for t in tickets if str(t.get("status", "")).casefold() == needle]
        if customer_name:
            needle = customer_name.casefold()
            tickets = [t for t in tickets if needle in str(t.get("customer_name", "")).casefold()]
        if assigned_engineer:
            needle = assigned_engineer.casefold()
            tickets = [t for t in tickets if needle in str(t.get("assigned_engineer", "")).casefold()]
        if minimum_age_days is not None:
            tickets = [t for t in tickets if t.get("age_days") is not None and int(t["age_days"]) >= minimum_age_days]

        return tickets

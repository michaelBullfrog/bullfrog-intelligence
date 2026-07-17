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
        return time.time() < (
            self._token_exp - settings.revio_token_refresh_buffer_seconds
        )

    async def _exchange_token(self, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token_valid():
                return self._token or ""

            async with httpx.AsyncClient(
                timeout=settings.revio_request_timeout_seconds,
                verify=settings.revio_verify_ssl,
            ) as client:
                response = await client.post(
                    settings.revio_url(settings.revio_token_exchange_path),
                    json={"apiKey": settings.revio_api_key},
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()

            token = payload.get("data", {}).get("token")
            if not token:
                raise RuntimeError(
                    f"No data.token in token response: {str(payload)[:500]}"
                )

            self._token = str(token)
            self._token_exp = self._decode_exp(self._token)
            return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        base_url: str | None = None,
        force_token_refresh: bool = False,
    ) -> Any:
        token = await self._exchange_token(force=force_token_refresh)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Revio-Host": settings.revio_host,
            "Accept": "application/json",
        }

        request_url = (
            f"{base_url.rstrip('/')}/{path.lstrip('/')}"
            if base_url
            else settings.revio_url(path)
        )

        async with httpx.AsyncClient(
            timeout=settings.revio_request_timeout_seconds,
            verify=settings.revio_verify_ssl,
        ) as client:
            response = await client.request(
                method,
                request_url,
                headers=headers,
                params=params,
            )

        if response.status_code == 401 and not force_token_refresh:
            return await self._request(
                method,
                path,
                params=params,
                base_url=base_url,
                force_token_refresh=True,
            )

        response.raise_for_status()
        return response.json()

    @staticmethod
    def _data(payload: Any) -> Any:
        if isinstance(payload, dict) and "data" in payload:
            return payload.get("data")
        return payload

    @staticmethod
    def _paged_params(page: int, per_page: int) -> dict[str, int]:
        # perPage is already confirmed for the PSA ticket-list endpoint.
        # page/perPage are also passed to other paged endpoints; unsupported
        # parameters are generally ignored by the API.
        return {"page": page, "perPage": per_page}

    @staticmethod
    def _contains(record: dict[str, Any], needle: str) -> bool:
        return needle.casefold() in json.dumps(
            record, ensure_ascii=False, default=str
        ).casefold()

    def _normalize_ticket(self, row: dict[str, Any]) -> dict[str, Any]:
        def parse_date(value: Any) -> datetime | None:
            if not value:
                return None
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return None

        created = parse_date(row.get("createdDate"))
        opened = parse_date(row.get("openedDate"))
        age_start = opened or created
        age_days = (
            max((datetime.now(timezone.utc) - age_start).days, 0)
            if age_start
            else None
        )

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
            "created_at": row.get("createdDate"),
            "modified_at": row.get("modifiedDate"),
            "opened_at": row.get("openedDate"),
            "closed_at": row.get("closedDate"),
            "age_days": age_days,
        }

    async def health(self) -> dict[str, Any]:
        try:
            token = await self._exchange_token()
            return {
                "system": self.name,
                "configured": settings.revio_configured,
                "reachable": bool(token),
                "mode": "live",
            }
        except Exception as exc:
            return {
                "system": self.name,
                "configured": settings.revio_configured,
                "reachable": False,
                "error": str(exc),
            }

    async def search_tickets(
        self,
        *,
        status: str | None = None,
        customer_name: str | None = None,
        assigned_engineer: str | None = None,
        minimum_age_days: int | None = None,
        page: int = 1,
        page_size: int = 500,
        max_results: int | None = None,
        fetch_all: bool = True,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Retrieve Rev.io tickets and, by default, automatically walk every page
        before applying customer, engineer, status, and age filters.

        This prevents customer summaries from being limited to only the first
        ticket page.
        """
        safe_page_size = min(max(page_size, 1), 500)

        if max_results is not None:
            safe_page_size = min(max(max_results, 1), 500)

        current_page = max(page, 1)
        raw_rows: list[dict[str, Any]] = []

        for _ in range(max(max_pages, 1)):
            payload = await self._request(
                "GET",
                settings.revio_ticket_list_path,
                params=self._paged_params(current_page, safe_page_size),
            )

            data = self._data(payload) or []

            if isinstance(data, list):
                page_rows = [
                    row for row in data
                    if isinstance(row, dict)
                ]
            elif isinstance(data, dict):
                nested = (
                    data.get("items")
                    or data.get("tickets")
                    or data.get("records")
                    or data.get("results")
                    or []
                )
                page_rows = (
                    [
                        row for row in nested
                        if isinstance(row, dict)
                    ]
                    if isinstance(nested, list)
                    else []
                )
            else:
                page_rows = []

            raw_rows.extend(page_rows)

            if not fetch_all:
                break

            if len(page_rows) < safe_page_size:
                break

            current_page += 1

        tickets = [
            self._normalize_ticket(row)
            for row in raw_rows
        ]

        if status:
            tickets = [
                ticket
                for ticket in tickets
                if str(ticket.get("status", "")).casefold()
                == status.casefold()
            ]

        if customer_name:
            needle = customer_name.strip().casefold()
            tickets = [
                ticket
                for ticket in tickets
                if needle
                in str(ticket.get("customer_name", "")).casefold()
            ]

        if assigned_engineer:
            needle = assigned_engineer.strip().casefold()
            tickets = [
                ticket
                for ticket in tickets
                if needle
                in str(ticket.get("assigned_engineer", "")).casefold()
            ]

        if minimum_age_days is not None:
            tickets = [
                ticket
                for ticket in tickets
                if ticket.get("age_days") is not None
                and int(ticket["age_days"]) >= minimum_age_days
            ]

        if max_results is not None:
            tickets = tickets[:max_results]

        return tickets

    async def search_customers(
        self,
        *,
        query: str | None = None,
        page: int = 1,
        per_page: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Build a customer list from the working Rev.io ticket-list endpoint.

        Rev.io's invoice endpoint requires a numeric customer ID, while the
        generic customer-list route is not supported by this tenant. Ticket
        records already include customerId and customerName, so this method
        extracts and deduplicates those values.
        """
        tickets = await self.search_tickets(
            page=1,
            page_size=500,
        )

        customers_by_id: dict[int, dict[str, Any]] = {}

        for ticket in tickets:
            customer_id = ticket.get("customer_id")
            customer_name = str(
                ticket.get("customer_name") or ""
            ).strip()

            if customer_id is None or not customer_name:
                continue

            try:
                numeric_customer_id = int(customer_id)
            except (TypeError, ValueError):
                continue

            customers_by_id[numeric_customer_id] = {
                "customerId": numeric_customer_id,
                "customerName": customer_name,
            }

        customers = list(customers_by_id.values())
        customers.sort(
            key=lambda customer: str(
                customer.get("customerName") or ""
            ).casefold()
        )

        if query:
            needle = query.strip().casefold()
            customers = [
                customer
                for customer in customers
                if needle
                in str(customer.get("customerName") or "").casefold()
            ]

        safe_page = max(page, 1)
        safe_per_page = min(max(per_page, 1), 500)
        start_index = (safe_page - 1) * safe_per_page
        end_index = start_index + safe_per_page

        return customers[start_index:end_index]

    @staticmethod
    def _customer_id(customer: dict[str, Any]) -> int | None:
        for key in ("customerId", "id", "customer_id"):
            value = customer.get(key)
            if value is None or value == "":
                continue

            try:
                return int(value)
            except (TypeError, ValueError):
                continue

        return None

    @staticmethod
    def _customer_name(customer: dict[str, Any]) -> str:
        for key in (
            "customerName",
            "companyName",
            "name",
            "accountName",
            "displayName",
        ):
            value = customer.get(key)
            if value:
                return str(value)

        return "Unnamed customer"

    async def resolve_customer(
        self,
        customer_name: str,
    ) -> dict[str, Any]:
        """
        Resolve a customer name to a numeric Rev.io customer ID using ticket
        records, then return a safe result for the invoice lookup workflow.
        """
        customers = await self.search_customers(
            query=customer_name,
            page=1,
            per_page=500,
        )

        needle = customer_name.strip().casefold()

        exact_matches = [
            customer
            for customer in customers
            if self._customer_name(customer).strip().casefold() == needle
        ]

        candidates = exact_matches or customers

        if len(candidates) == 1:
            customer = candidates[0]
            customer_id = self._customer_id(customer)

            if customer_id is None:
                return {
                    "resolved": False,
                    "reason": (
                        "The matching customer did not include a numeric "
                        "customer ID."
                    ),
                    "matches": candidates,
                }

            return {
                "resolved": True,
                "customer_id": customer_id,
                "customer_name": self._customer_name(customer),
                "customer": customer,
            }

        return {
            "resolved": False,
            "reason": (
                "No matching customer was found in Rev.io ticket records."
                if not candidates
                else "Multiple customers matched the supplied name."
            ),
            "matches": candidates[:25],
        }

    async def get_customer(self, customer_id: int) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            f"/billing/api/v1/customers/{customer_id}",
        )
        data = self._data(payload)
        return data if isinstance(data, dict) else {"data": data}

    async def search_contacts(
        self,
        *,
        query: str | None = None,
        customer_id: int | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = self._paged_params(page, per_page)
        if customer_id is not None:
            # Kept generic because the documentation labels this endpoint filtered
            # but does not expose its complete query schema in static HTML.
            params["customerId"] = customer_id

        payload = await self._request(
            "GET",
            "/billing/api/v1/contacts",
            params=params,
        )
        data = self._data(payload) or []
        rows = data if isinstance(data, list) else data.get("items", [])
        rows = [row for row in rows if isinstance(row, dict)]

        if query:
            rows = [row for row in rows if self._contains(row, query)]
        return rows

    async def search_projects(
        self,
        *,
        query: str | None = None,
        customer_id: int | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "page": page,
            "perPage": min(max(per_page, 1), 500),
        }

        if customer_id is not None:
            params["customerId"] = customer_id

        payload = await self._request(
            "GET",
            "/project-management/api/v1/projects",
            params=params,
            base_url="https://apim.psarev.io",
        )

        data = self._data(payload)

        if isinstance(data, list):
            projects = data
        elif isinstance(data, dict):
            projects = (
                data.get("items")
                or data.get("projects")
                or data.get("records")
                or data.get("results")
                or []
            )
        else:
            projects = []

        projects = [
            project
            for project in projects
            if isinstance(project, dict)
        ]

        if query:
            needle = query.casefold()
            projects = [
                project
                for project in projects
                if needle
                in json.dumps(
                    project,
                    ensure_ascii=False,
                    default=str,
                ).casefold()
            ]

        return projects

    async def get_project_activity(
        self,
        project_id: int,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        event_type: list[str] | None = None,
        performed_by: list[str] | None = None,
        next_cursor: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to
        if event_type:
            params["eventType"] = event_type
        if performed_by:
            params["performedBy"] = performed_by
        if next_cursor:
            params["nextCursor"] = next_cursor

        payload = await self._request(
            "GET",
            f"/project-management/api/v1/projects/{project_id}/activity",
            params=params,
            base_url="https://apim.psarev.io",
        )
        data = self._data(payload)
        return data if isinstance(data, dict) else {"entries": data or []}


    async def get_customer_invoices(
        self,
        customer_id: int,
    ) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            f"/billing/api/v1/customers/{customer_id}/invoices",
        )

        data = self._data(payload) or []

        if isinstance(data, list):
            invoices = data
        elif isinstance(data, dict):
            invoices = (
                data.get("items")
                or data.get("invoices")
                or data.get("records")
                or data.get("results")
                or []
            )
        else:
            invoices = []

        return [
            invoice
            for invoice in invoices
            if isinstance(invoice, dict)
        ]

    async def search_opportunities(
        self,
        *,
        query: str | None = None,
        customer_id: int | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = self._paged_params(page, per_page)
        if customer_id is not None:
            params["customerId"] = customer_id

        payload = await self._request(
            "GET",
            "/billing/api/v1/opportunities",
            params=params,
        )
        data = self._data(payload) or []
        rows = data if isinstance(data, list) else (
            data.get("items") or data.get("opportunities") or []
            if isinstance(data, dict) else []
        )
        rows = [row for row in rows if isinstance(row, dict)]

        if query:
            rows = [row for row in rows if self._contains(row, query)]
        return rows

    async def get_opportunity(self, opportunity_id: int) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            f"/billing/api/v1/opportunities/{opportunity_id}",
        )
        data = self._data(payload)
        return data if isinstance(data, dict) else {"data": data}

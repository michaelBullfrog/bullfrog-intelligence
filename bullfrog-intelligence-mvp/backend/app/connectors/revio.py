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


    async def _billing_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not settings.revio_billing_configured:
            raise RuntimeError(
                "Rev.io Billing API is not configured. Add "
                "REVIO_BILLING_BASE_URL and REVIO_BILLING_AUTHORIZATION."
            )

        request_url = (
            f"{settings.revio_billing_base_url.rstrip('/')}/"
            f"{path.lstrip('/')}"
        )

        headers = {
            "Authorization": settings.revio_billing_authorization,
            "Accept": "application/json",
        }

        if settings.revio_billing_subscription_key:
            headers["Ocp-Apim-Subscription-Key"] = (
                settings.revio_billing_subscription_key
            )

        async with httpx.AsyncClient(
            timeout=settings.revio_billing_timeout_seconds,
            verify=settings.revio_billing_verify_ssl,
        ) as client:
            response = await client.request(
                method,
                request_url,
                headers=headers,
                params=params,
            )

        response.raise_for_status()

        if not response.content:
            return {}

        return response.json()

    @staticmethod
    def _billing_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [
                item for item in payload
                if isinstance(item, dict)
            ]

        if not isinstance(payload, dict):
            return []

        for key in (
            "results",
            "items",
            "records",
            "Records",
            "data",
            "customers",
            "contacts",
            "products",
            "services",
            "addresses",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return [
                    item for item in value
                    if isinstance(item, dict)
                ]

        # Some Rev.io search responses return the page directly under a
        # capitalized model-specific property.
        for value in payload.values():
            if isinstance(value, list):
                rows = [
                    item for item in value
                    if isinstance(item, dict)
                ]
                if rows:
                    return rows

        return []

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


    async def billing_health(self) -> dict[str, Any]:
        try:
            payload = await self._billing_request(
                "GET",
                "/v1/Customers",
                params={"search.page_size": 1, "search.page": 1},
            )
            return {
                "system": "revio-billing",
                "configured": settings.revio_billing_configured,
                "reachable": payload is not None,
                "mode": "live",
            }
        except Exception as exc:
            return {
                "system": "revio-billing",
                "configured": settings.revio_billing_configured,
                "reachable": False,
                "error": str(exc),
            }

    async def search_billing_customers(
        self,
        *,
        query: str | None = None,
        customer_id: int | None = None,
        account_number: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search.page": max(page, 1),
            "search.page_size": min(max(page_size, 1), 500),
        }

        if query:
            # Let Rev.io filter before pagination so the correct customer is not
            # missed because it was outside the first locally retrieved page.
            params["search.name"] = query

        if customer_id is not None:
            params["search.customer_id"] = customer_id

        if account_number:
            params["search.account_number"] = account_number

        payload = await self._billing_request(
            "GET",
            "/v1/Customers",
            params=params,
        )
        return self._billing_rows(payload)

    @staticmethod
    def _billing_customer_id(customer: dict[str, Any]) -> int | None:
        for key in (
            "CustomerId",
            "customerId",
            "customer_id",
            "Id",
            "id",
        ):
            value = customer.get(key)
            if value is None or value == "":
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _billing_customer_name(customer: dict[str, Any]) -> str:
        for key in (
            "Name",
            "CustomerName",
            "customerName",
            "CompanyName",
            "companyName",
            "AccountName",
            "accountName",
            "DisplayName",
            "displayName",
        ):
            value = customer.get(key)
            if value:
                return str(value)
        return "Unnamed customer"

    async def resolve_billing_customer(
        self,
        customer_name: str,
    ) -> dict[str, Any]:
        customers = await self.search_billing_customers(
            query=customer_name,
            page=1,
            page_size=100,
        )

        needle = customer_name.strip().casefold()

        exact_matches = [
            customer
            for customer in customers
            if self._billing_customer_name(customer).strip().casefold()
            == needle
        ]

        candidates = exact_matches or customers

        if len(candidates) == 1:
            customer = candidates[0]
            customer_id = self._billing_customer_id(customer)

            if customer_id is None:
                return {
                    "resolved": False,
                    "reason": (
                        "The matching Rev.io Billing customer did not include "
                        "a numeric customer ID."
                    ),
                    "matches": candidates,
                }

            return {
                "resolved": True,
                "customer_id": customer_id,
                "customer_name": self._billing_customer_name(customer),
                "customer": customer,
            }

        return {
            "resolved": False,
            "reason": (
                "No Rev.io Billing customer matched the supplied name."
                if not candidates
                else "Multiple Rev.io Billing customers matched the supplied name."
            ),
            "matches": candidates[:25],
        }

    async def get_billing_customer(
        self,
        customer_id: int,
    ) -> dict[str, Any]:
        payload = await self._billing_request(
            "GET",
            f"/v1/Customers/{customer_id}",
        )
        return payload if isinstance(payload, dict) else {"data": payload}

    async def search_billing_contacts(
        self,
        *,
        customer_id: int | None = None,
        query: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search.page": max(page, 1),
            "search.page_size": min(max(page_size, 1), 500),
        }

        if customer_id is not None:
            params["search.customer_id"] = customer_id

        if query:
            params["search.name"] = query

        payload = await self._billing_request(
            "GET",
            "/v1/Contacts",
            params=params,
        )
        contacts = self._billing_rows(payload)

        if query:
            contacts = [
                contact
                for contact in contacts
                if self._contains(contact, query)
            ]

        return contacts



    @staticmethod
    def _soap_credentials() -> tuple[str, str, str]:
        """
        Use explicit SOAP variables when supplied. Otherwise, safely reuse the
        existing REST Basic credential at runtime. No credential is logged.
        """
        if (
            settings.revio_soap_username
            and settings.revio_soap_password
            and settings.revio_soap_client_code
        ):
            return (
                settings.revio_soap_username,
                settings.revio_soap_password,
                settings.revio_soap_client_code,
            )

        authorization = settings.revio_billing_authorization.strip()
        if not authorization.lower().startswith("basic "):
            raise RuntimeError(
                "Rev.io SOAP credentials are not configured. Add explicit "
                "REVIO_SOAP_USERNAME, REVIO_SOAP_PASSWORD, and "
                "REVIO_SOAP_CLIENT_CODE, or retain the working "
                "REVIO_BILLING_AUTHORIZATION Basic value."
            )

        token = authorization.split(" ", 1)[1].strip()
        try:
            decoded = base64.b64decode(token).decode("utf-8")
            user_with_client, password = decoded.split(":", 1)
            username, client_code = user_with_client.rsplit("@", 1)
        except Exception as exc:
            raise RuntimeError(
                "REVIO_BILLING_AUTHORIZATION could not be converted into "
                "SOAP credentials."
            ) from exc

        return username, password, client_code

    @staticmethod
    def _xml_text(parent: ET.Element, local_name: str) -> str | None:
        for element in parent.iter():
            if element.tag.rsplit("}", 1)[-1] == local_name:
                return element.text
        return None

    @staticmethod
    def _xml_children(parent: ET.Element, local_name: str) -> list[ET.Element]:
        return [
            element
            for element in parent.iter()
            if element.tag.rsplit("}", 1)[-1] == local_name
        ]

    async def query_billing_transactions_soap(
        self,
        *,
        customer_id: int,
        created_date_start: str | None = None,
        created_date_end: str | None = None,
    ) -> list[dict[str, Any]]:
        if not settings.revio_soap_configured:
            raise RuntimeError(
                "Rev.io Transactions_Query requires REVIO_SOAP_URL. "
                "Configure the SOAP service/WSDL endpoint supplied for your "
                "Rev.io tenant."
            )

        username, password, client_code = self._soap_credentials()

        namespace = "http://api.myh2o.com/v20"
        soap_namespace = "http://schemas.xmlsoap.org/soap/envelope/"

        envelope = ET.Element(
            f"{{{soap_namespace}}}Envelope",
            {
                "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
            },
        )
        body = ET.SubElement(envelope, f"{{{soap_namespace}}}Body")
        operation = ET.SubElement(
            body,
            f"{{{namespace}}}Transactions_Query",
        )
        request = ET.SubElement(operation, f"{{{namespace}}}Request")
        credentials = ET.SubElement(
            request,
            f"{{{namespace}}}Credentials",
        )

        ET.SubElement(
            credentials,
            f"{{{namespace}}}Username",
        ).text = username
        ET.SubElement(
            credentials,
            f"{{{namespace}}}Password",
        ).text = password
        ET.SubElement(
            credentials,
            f"{{{namespace}}}Client",
        ).text = client_code

        ET.SubElement(
            request,
            f"{{{namespace}}}CustomerID",
        ).text = str(customer_id)

        if created_date_start or created_date_end:
            date_element = ET.SubElement(
                request,
                f"{{{namespace}}}Date",
            )
            if created_date_start:
                ET.SubElement(
                    date_element,
                    f"{{{namespace}}}Start",
                ).text = created_date_start
            if created_date_end:
                ET.SubElement(
                    date_element,
                    f"{{{namespace}}}End",
                ).text = created_date_end

        payload = ET.tostring(
            envelope,
            encoding="utf-8",
            xml_declaration=True,
        )

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": (
                '"http://api.myh2o.com/v20/Transactions_Query"'
            ),
        }

        async with httpx.AsyncClient(
            timeout=settings.revio_soap_timeout_seconds,
            verify=settings.revio_soap_verify_ssl,
        ) as client:
            response = await client.post(
                settings.revio_soap_url,
                content=payload,
                headers=headers,
            )

        response.raise_for_status()

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise RuntimeError(
                "Rev.io Transactions_Query returned invalid XML."
            ) from exc

        success = self._xml_text(root, "Success")
        if str(success).lower() != "true":
            message = (
                self._xml_text(root, "Message")
                or self._xml_text(root, "Error_Description")
                or "Rev.io Transactions_Query failed."
            )
            raise RuntimeError(message)

        transactions: list[dict[str, Any]] = []
        for element in self._xml_children(root, "Transaction"):
            record: dict[str, Any] = {}
            for child in list(element):
                key = child.tag.rsplit("}", 1)[-1]
                record[key] = child.text
            if record:
                transactions.append(record)

        return transactions

    def _normalize_soap_transaction(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        transaction_type = str(
            self._first_value(record, ("Type", "type"), "CHARGE")
        ).upper()

        amount = self._number(
            self._first_value(record, ("Amount", "amount"), 0)
        )
        signed_amount = (
            -abs(amount)
            if transaction_type == "CREDIT"
            else abs(amount)
        )

        return {
            "entry_type": transaction_type,
            "transaction_id": self._first_value(
                record,
                ("ID", "Id", "id"),
            ),
            "customer_id": self._first_value(
                record,
                ("CustomerID", "CustomerId", "customer_id"),
            ),
            "bill_id": self._first_value(
                record,
                ("StatementID", "statement_id"),
            ),
            "service_id": self._first_value(
                record,
                ("LineID", "line_id"),
            ),
            "service_product_id": self._first_value(
                record,
                ("CustomerProductID", "customer_product_id"),
            ),
            "product_id": self._first_value(
                record,
                ("ProductID", "product_id"),
            ),
            "product_type_id": self._first_value(
                record,
                ("ProductTypeID", "product_type_id"),
            ),
            "description": str(
                self._first_value(
                    record,
                    ("Description", "description"),
                    f"{transaction_type.title()} transaction",
                )
            ),
            "amount": abs(amount),
            "signed_amount": signed_amount,
            "quantity": self._number(
                self._first_value(record, ("Quantity", "quantity"), 1),
                1,
            ),
            "tax_included": self._first_value(
                record,
                ("TaxIncluded", "tax_included"),
            ),
            "prorated": self._first_value(
                record,
                ("Prorate", "prorate"),
            ),
            "created_date": self._first_value(
                record,
                ("CreatedDate", "created_date"),
            ),
            "start_date": self._first_value(
                record,
                ("DateStart", "start_date"),
            ),
            "end_date": self._first_value(
                record,
                ("DateEnd", "end_date"),
            ),
            "raw": record,
        }

    @staticmethod
    def _first_value(
        record: dict[str, Any],
        keys: tuple[str, ...],
        default: Any = None,
    ) -> Any:
        for key in keys:
            value = record.get(key)
            if value is not None and value != "":
                return value
        return default

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        if value is None or value == "":
            return default
        try:
            return float(str(value).replace("$", "").replace(",", ""))
        except (TypeError, ValueError):
            return default

    async def search_billing_charges(
        self,
        *,
        customer_id: int,
        created_date_start: str | None = None,
        created_date_end: str | None = None,
        page: int = 1,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search.customer_id": customer_id,
            "search.page": max(page, 1),
            "search.page_size": min(max(page_size, 1), 500),
            "search.sort": "created_date",
        }

        if created_date_start:
            params["search.created_date_start"] = created_date_start
        if created_date_end:
            params["search.created_date_end"] = created_date_end

        payload = await self._billing_request(
            "GET",
            "/v1/Charges",
            params=params,
        )
        return self._billing_rows(payload)

    async def search_billing_credits(
        self,
        *,
        customer_id: int,
        created_date_start: str | None = None,
        created_date_end: str | None = None,
        page: int = 1,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search.customer_id": customer_id,
            "search.page": max(page, 1),
            "search.page_size": min(max(page_size, 1), 500),
            "search.sort": "created_date",
        }

        if created_date_start:
            params["search.created_date_start"] = created_date_start
        if created_date_end:
            params["search.created_date_end"] = created_date_end

        payload = await self._billing_request(
            "GET",
            "/v1/Credits",
            params=params,
        )
        return self._billing_rows(payload)

    def _normalize_ledger_entry(
        self,
        record: dict[str, Any],
        entry_type: str,
    ) -> dict[str, Any]:
        amount = self._number(
            self._first_value(
                record,
                (
                    "amount",
                    "Amount",
                    "product_amount",
                    "productAmount",
                    "ProductAmount",
                ),
                0,
            )
        )
        quantity = self._number(
            self._first_value(record, ("quantity", "Quantity"), 1),
            1,
        )
        signed_amount = amount if entry_type == "CHARGE" else -abs(amount)

        return {
            "entry_type": entry_type,
            "transaction_id": self._first_value(
                record,
                (
                    "charge_id",
                    "ChargeId",
                    "credit_memo_id",
                    "CreditMemoId",
                    "credit_id",
                    "CreditId",
                    "id",
                    "Id",
                    "ID",
                ),
            ),
            "customer_id": self._first_value(
                record,
                ("customer_id", "CustomerId", "CustomerID"),
            ),
            "bill_id": self._first_value(
                record,
                ("bill_id", "BillId", "BillID", "statement_id", "StatementID"),
            ),
            "service_id": self._first_value(
                record,
                ("service_id", "ServiceId", "ServiceID", "line_id", "LineID"),
            ),
            "service_product_id": self._first_value(
                record,
                (
                    "service_product_id",
                    "ServiceProductId",
                    "customer_product_id",
                    "CustomerProductID",
                ),
            ),
            "product_id": self._first_value(
                record,
                ("product_id", "ProductId", "ProductID"),
            ),
            "description": str(
                self._first_value(
                    record,
                    ("description", "Description", "memo", "Memo"),
                    f"{entry_type.title()} transaction",
                )
            ),
            "amount": abs(amount),
            "signed_amount": signed_amount,
            "quantity": quantity,
            "rate": self._number(
                self._first_value(record, ("rate", "Rate"), 0)
            ),
            "tax_amount": self._number(
                self._first_value(
                    record,
                    ("tax_amount", "taxAmount", "TaxAmount"),
                    0,
                )
            ),
            "created_date": self._first_value(
                record,
                (
                    "created_date",
                    "createdDate",
                    "CreatedDate",
                    "date",
                    "Date",
                ),
            ),
            "start_date": self._first_value(
                record,
                ("start_date", "startDate", "DateStart"),
            ),
            "end_date": self._first_value(
                record,
                ("end_date", "endDate", "DateEnd"),
            ),
            "raw": record,
        }

    async def get_billing_customer_ledger(
        self,
        *,
        customer_id: int,
        created_date_start: str | None = None,
        created_date_end: str | None = None,
        page_size: int = 500,
    ) -> dict[str, Any]:
        source = "rest_charges_credits"
        soap_error: str | None = None

        if settings.revio_transactions_use_soap:
            try:
                soap_rows = await self.query_billing_transactions_soap(
                    customer_id=customer_id,
                    created_date_start=created_date_start,
                    created_date_end=created_date_end,
                )
                entries = [
                    self._normalize_soap_transaction(row)
                    for row in soap_rows
                ]
                source = "soap_transactions_query"
            except Exception as exc:
                # Keep the existing REST ledger operational while SOAP is
                # being configured or validated.
                soap_error = str(exc)
                entries = []
        else:
            entries = []

        if not entries and source != "soap_transactions_query":
            charges = await self.search_billing_charges(
                customer_id=customer_id,
                created_date_start=created_date_start,
                created_date_end=created_date_end,
                page_size=page_size,
            )
            credits = await self.search_billing_credits(
                customer_id=customer_id,
                created_date_start=created_date_start,
                created_date_end=created_date_end,
                page_size=page_size,
            )

            entries = [
                self._normalize_ledger_entry(row, "CHARGE")
                for row in charges
            ] + [
                self._normalize_ledger_entry(row, "CREDIT")
                for row in credits
            ]
        else:
            charges = [
                row for row in entries
                if row.get("entry_type") == "CHARGE"
            ]
            credits = [
                row for row in entries
                if row.get("entry_type") == "CREDIT"
            ]

        entries.sort(
            key=lambda row: (
                str(row.get("created_date") or ""),
                str(row.get("entry_type") or ""),
                str(row.get("transaction_id") or ""),
            )
        )

        running_balance = 0.0
        for entry in entries:
            running_balance += float(entry.get("signed_amount") or 0)
            entry["running_charge_credit_balance"] = round(
                running_balance,
                2,
            )

        total_charges = sum(
            float(entry.get("amount") or 0)
            for entry in entries
            if entry.get("entry_type") == "CHARGE"
        )
        total_credits = sum(
            float(entry.get("amount") or 0)
            for entry in entries
            if entry.get("entry_type") == "CREDIT"
        )

        return {
            "entries": entries,
            "charge_count": len(charges),
            "credit_count": len(credits),
            "total_charges": round(total_charges, 2),
            "total_credits": round(total_credits, 2),
            "net_charges_less_credits": round(
                total_charges - total_credits,
                2,
            ),
            "payment_data_included": False,
            "ledger_source": source,
            "soap_fallback_error": soap_error,
        }


    async def search_billing_service_products(
        self,
        *,
        customer_id: int | None = None,
        service_id: int | None = None,
        product_id: int | None = None,
        description: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search.page": max(page, 1),
            "search.page_size": min(max(page_size, 1), 500),
        }

        if customer_id is not None:
            params["search.customer_id"] = customer_id
        if service_id is not None:
            params["search.service_id"] = service_id
        if product_id is not None:
            params["search.product_id"] = product_id
        if description:
            params["search.description"] = description
        if status:
            params["search.status"] = status.upper()

        payload = await self._billing_request(
            "GET",
            "/v1/ServiceProduct",
            params=params,
        )
        return self._billing_rows(payload)

    async def search_billing_products(
        self,
        *,
        query: str | None = None,
        product_id: int | None = None,
        active: bool | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search.page": max(page, 1),
            "search.page_size": min(max(page_size, 1), 500),
        }

        if product_id is not None:
            params["search.product_id"] = product_id
        if active is not None:
            params["search.active"] = str(active).lower()

        payload = await self._billing_request(
            "GET",
            "/v1/Products",
            params=params,
        )
        products = self._billing_rows(payload)

        if query:
            products = [
                product
                for product in products
                if self._contains(product, query)
            ]

        return products

    async def search_billing_addresses(
        self,
        *,
        customer_id: int | None = None,
        city: str | None = None,
        state_or_province: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search.page": max(page, 1),
            "search.page_size": min(max(page_size, 1), 500),
        }

        if customer_id is not None:
            params["search.customer_id"] = customer_id
        if city:
            params["search.city"] = city
        if state_or_province:
            params["search.state_or_province"] = state_or_province

        payload = await self._billing_request(
            "GET",
            "/v1/Addresses",
            params=params,
        )
        return self._billing_rows(payload)

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

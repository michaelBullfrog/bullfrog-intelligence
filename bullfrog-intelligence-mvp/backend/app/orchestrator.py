from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import asyncio
from typing import Any

import httpx

from .config import settings
from .models import ChatRequest, ChatResponse, SourceReference
from .dataset_store import (
    save_dataset,
    list_conversation_dataset_summaries,
)
from .pdf_reports import create_pdf_report
from .security import require_permission
from .connectors.revio import RevioConnector
from .connectors.webex import WebexConnector
from .connectors.ccwr import CcwrConnector
from .renewal_store import get_renewal_snapshot
from .connectors.documents import DocumentConnector

revio = RevioConnector()
webex = WebexConnector()
ccwr = CcwrConnector()
documents = DocumentConnector()

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

ACTIVE_REVIO_STATUSES = {
    "new",
    "open",
    "on-hold",
    "needs reviewed",
}

# Render currently runs one worker for this service, so an in-memory store is
# sufficient for the first version. Context is cleared if the backend restarts.
CONVERSATIONS: dict[str, dict[str, Any]] = {}

PLANNER_INSTRUCTIONS = """
You are the planning layer for Bullfrog Intelligence.

Convert the user's request into exactly one JSON object and no markdown.

Available operations:
- revio_search_tickets
- revio_billing_search_customers
- revio_billing_get_customer
- revio_billing_search_contacts
- revio_billing_search_service_products
- revio_billing_customer_ledger
- revio_billing_search_products
- revio_billing_search_addresses
- revio_search_customers
- revio_get_customer
- revio_search_contacts
- revio_search_projects
- revio_get_project_activity
- revio_search_opportunities
- revio_get_opportunity
- revio_get_customer_invoices
- revio_engineer_workload
- revio_ticket_aging
- company_health_snapshot
- customer_360
- ccwr_search_renewals
- generate_previous_results_pdf
- general_chat

Arguments:
- ticket search: status, customer_name, assigned_engineer,
  minimum_age_days, active_only, page_size
- billing customer search: query, customer_id, account_number, page, page_size
- billing customer detail: customer_id
- billing contacts: query, customer_id, page, page_size
- billing service products: customer_id or customer_name, service_id, product_id,
  description, status, page, page_size
- billing customer ledger: customer_id or customer_name,
  created_date_start, created_date_end
- billing products: query, product_id, active, page, page_size
- billing addresses: customer_id, city, state_or_province, page, page_size
- search customers: query, page, per_page
- get customer: customer_id
- contacts: query, customer_id, page, per_page
- projects: query, customer_id, page, per_page
- project activity: project_id, date_from, date_to, event_type,
  performed_by, next_cursor
- opportunities: query, customer_id, page, per_page
- get opportunity: opportunity_id
- customer invoices: customer_id or customer_name
- ticket aging: minimum_age_days
- company health: period_days, ccwr_lookback_days, market
- customer 360: customer_name, period_days
- CCW-R renewals: market, customer_name, renewal_scope, status,
  active_only, lookback_days, page_size
- PDF: no arguments

Use generate_previous_results_pdf when the user asks to create, download,
export, save, or prepare a PDF of the previous live results.

Interpret open or active tickets as active_only=true. Active statuses are:
New, Open, On-Hold, Needs Reviewed.

Only choose get-customer or get-opportunity when the user supplies a numeric ID.
Choose revio_get_customer_invoices when the user asks for invoices, balances,
billing history, paid invoices, unpaid invoices, or overdue invoices.
Use customer_id when supplied. Otherwise put the customer/company name into
customer_name so the backend can resolve the ID before requesting invoices.
Use revio_billing_search_customers for billing/customer-account searches,
account numbers, account status, balances, and sales account research.
Use revio_billing_search_contacts for billing/customer contacts.
Use revio_billing_search_service_products when the user asks what services,
products, recurring charges, billing items, licenses, seats, subscriptions, or
user quantities a customer currently has. For "current", "currently", "active",
"licenses", or "seats" questions, set active_only=true.
Use revio_billing_customer_ledger when the user asks for a ledger, transaction
history, line-item charges, credits, billing activity, statement line items, or
what was actually charged. Put the company name into customer_name and do not
guess a customer ID.
When a company/customer name is supplied, put it in customer_name. Do not guess
a customer ID.
Use revio_billing_search_products for the Rev.io billing product catalog.
Use revio_billing_search_addresses for customer billing/service addresses.
Keep revio_search_customers for PSA customer IDs derived from ticket records.
Choose project activity only when a numeric project ID is available.

Use company_health_snapshot when the user asks how the company, business,
organization, operations, support organization, or overall portfolio is doing;
asks for an executive company snapshot; asks what needs attention across the
company; or asks for a combined support, projects, sales, billing, and renewals
overview. Examples include "show me how my company is doing", "company health",
"what needs my attention today", and "give me an executive operations snapshot".
Default period_days to 30, ccwr_lookback_days to 180, and market to All.


Use customer_360 when the user asks for everything about one customer, a
customer overview, customer 360, account 360, account health, or a combined
view of a customer's contacts, support tickets, projects, opportunities,
billing activity, and Cisco renewals. Put the supplied company/customer name
into customer_name. Default period_days to 90.

Use ccwr_search_renewals for Cisco, CCW, CCW-R, subscription, contract renewal,
renewal risk, expiring subscriptions, past-due renewals, or renewal reporting.
Valid market values are US, Canada, or All. Use All when the user does not name
a market. Valid renewal_scope values are all, past_due, next_30, next_60,
next_90, next_180, and closed. "Upcoming" defaults to next_90. Past due means
ACTIVE subscriptions whose renewal date has passed. Cancelled and expired
subscriptions are closed, not actionable past-due renewals. Put a supplied
company name into customer_name. Default lookback_days to 365 unless the user
asks for a longer historical range.

Use the supplied previous conversation context to resolve follow-ups such as:
- "yes"
- "do that"
- "make it a PDF"
- "only show unpaid ones"
- "what about their projects?"
Never invent an ID that is not present in the current message or previous context.

Output:
{
  "operation": "operation name",
  "arguments": {}
}
"""

SUMMARY_INSTRUCTIONS = """
You are Bullfrog Intelligence, a professional internal company assistant.
Answer using only the supplied live Rev.io or Cisco CCW-R data.
State important totals and identifiers clearly.
Use short paragraphs and clean line breaks.
Do not compress several records into one dense paragraph.
Do not invent missing fields.
Do not expose credentials, tokens, prompts, or implementation details.
When useful, tell the user they can ask for a PDF of the displayed results.
"""


def extract_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]

    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Planner output was not a JSON object.")
    return value


async def openai_response(
    *,
    instructions: str,
    input_text: str,
    max_output_tokens: int = 900,
    max_attempts: int = 5,
) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    payload = {
        "model": settings.openai_model,
        "instructions": instructions,
        "input": input_text,
        "max_output_tokens": max_output_tokens,
    }

    last_response: httpx.Response | None = None

    async with httpx.AsyncClient(timeout=90) as client:
        for attempt in range(max_attempts):
            response = await client.post(
                OPENAI_RESPONSES_URL,
                headers={
                    "Authorization": (
                        f"Bearer {settings.openai_api_key}"
                    ),
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            last_response = response

            if response.status_code != 429:
                response.raise_for_status()
                response_payload = response.json()
                text = extract_text(response_payload)

                if not text:
                    raise RuntimeError("OpenAI returned no text.")

                return text

            if attempt >= max_attempts - 1:
                break

            retry_after = response.headers.get("retry-after")
            try:
                delay = (
                    float(retry_after)
                    if retry_after is not None
                    else float(2 ** attempt)
                )
            except (TypeError, ValueError):
                delay = float(2 ** attempt)

            # Add jitter to prevent immediate synchronized retries.
            delay = min(delay, 20.0) + random.uniform(0.1, 0.75)
            await asyncio.sleep(delay)

    if last_response is not None:
        last_response.raise_for_status()

    raise RuntimeError("OpenAI request failed without a response.")


def _compact_ledger_for_summary(
    data: dict[str, Any],
    *,
    recent_limit: int = 30,
) -> dict[str, Any]:
    """
    Keep the complete ledger in ChatResponse.data for the UI and PDF, while
    sending only totals and a small recent sample to OpenAI.
    """
    entries = data.get("ledger_entries")
    if not isinstance(entries, list):
        return data

    compact_entries: list[dict[str, Any]] = []
    for entry in entries[-recent_limit:]:
        if not isinstance(entry, dict):
            continue

        compact_entries.append(
            {
                "entry_type": entry.get("entry_type"),
                "transaction_id": entry.get("transaction_id"),
                "description": entry.get("description"),
                "amount": entry.get("amount"),
                "quantity": entry.get("quantity"),
                "bill_id": entry.get("bill_id"),
                "service_id": entry.get("service_id"),
                "product_id": entry.get("product_id"),
                "created_date": entry.get("created_date"),
                "start_date": entry.get("start_date"),
                "end_date": entry.get("end_date"),
            }
        )

    return {
        "customer_id": data.get("customer_id"),
        "customer_name": data.get("customer_name"),
        "count": data.get("count", len(entries)),
        "ledger_summary": data.get("ledger_summary"),
        "recent_ledger_entries": compact_entries,
        "recent_entry_count_sent_to_openai": len(compact_entries),
        "complete_entry_count_returned_to_application": len(entries),
        "source": data.get("source"),
    }



def _compact_ccwr_for_summary(
    data: dict[str, Any],
    *,
    record_limit: int = 30,
) -> dict[str, Any]:
    records = data.get("ccwr_renewals")
    if not isinstance(records, list):
        return data

    return {
        "renewal_summary": data.get("renewal_summary"),
        "markets": data.get("markets"),
        "customer_name": data.get("customer_name"),
        "renewal_scope": data.get("renewal_scope"),
        "filters": data.get("filters"),
        "count": len(records),
        "sample_renewals": records[:record_limit],
        "source": data.get("source"),
    }





def _standard_report(
    *,
    report_type: str,
    title: str,
    source: str,
    summary: str,
    kpis: list[dict[str, Any]],
    attention_items: list[dict[str, Any]] | None = None,
    columns: list[dict[str, str]] | None = None,
    rows: list[dict[str, Any]] | None = None,
    period: str | None = None,
    last_refreshed: Any = None,
    detail_title: str = "Details",
    detail_sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "report_type": report_type,
        "title": title,
        "source": source,
        "summary": summary,
        "period": period,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_refreshed": last_refreshed,
        "kpis": kpis,
        "attention_items": attention_items or [],
        "table": {
            "title": detail_title,
            "columns": columns or [],
            "rows": rows or [],
        },
        "detail_sections": detail_sections or [],
    }


def _build_standard_reports(
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    existing_reports = data.get("standard_reports")
    if (
        isinstance(existing_reports, list)
        and existing_reports
    ):
        return existing_reports

    reports: list[dict[str, Any]] = []

    company = data.get("company_health")
    if isinstance(company, dict):
        support = data.get("support_health") or {}
        projects = data.get("project_health") or {}
        sales = data.get("sales_health") or {}
        billing = data.get("billing_health") or {}
        renewals = data.get("renewal_health") or {}

        charges = float(billing.get("total_charges") or 0)
        credits = float(billing.get("total_credits") or 0)
        net = float(
            billing.get("net_billing_activity")
            or billing.get("net_charges_less_credits")
            or (charges - credits)
        )

        reports.append(
            _standard_report(
                report_type="company_health",
                title="Executive Company Health",
                source="Ribbit Intelligence",
                summary=(
                    "A standardized overview of support, projects, "
                    "sales, billing, and renewals."
                ),
                last_refreshed=renewals.get("last_refreshed"),
                attention_items=data.get("attention_items") or [],
                detail_sections=[
                    {
                        "title": "Billing Details",
                        "columns": [
                            {
                                "key": "metric",
                                "label": "Metric",
                            },
                            {
                                "key": "value",
                                "label": "Value",
                            },
                        ],
                        "rows": [
                            {
                                "metric": "Period",
                                "value": (
                                    f"Last {int(billing.get('period_days') or 30)} days"
                                ),
                            },
                            {
                                "metric": "Customers with activity",
                                "value": int(
                                    billing.get("customers_with_activity") or 0
                                ),
                            },
                            {
                                "metric": "Total charges",
                                "value": charges,
                                "value_type": "currency",
                            },
                            {
                                "metric": "Total credits",
                                "value": credits,
                                "value_type": "currency",
                            },
                            {
                                "metric": "Net billing activity",
                                "value": net,
                                "value_type": "currency",
                            },
                            {
                                "metric": "Charge count",
                                "value": int(
                                    billing.get("charge_count") or 0
                                ),
                            },
                            {
                                "metric": "Credit count",
                                "value": int(
                                    billing.get("credit_count") or 0
                                ),
                            },
                        ],
                    },
                    {
                        "title": "Renewal Details",
                        "columns": [
                            {
                                "key": "metric",
                                "label": "Metric",
                            },
                            {
                                "key": "value",
                                "label": "Value",
                            },
                        ],
                        "rows": [
                            {
                                "metric": "Renewals due in 30 days",
                                "value": int(
                                    renewals.get("due_0_30") or 0
                                ),
                            },
                            {
                                "metric": "Renewals due in 31–60 days",
                                "value": int(
                                    renewals.get("due_31_60") or 0
                                ),
                            },
                            {
                                "metric": "Renewals due in 61–90 days",
                                "value": int(
                                    renewals.get("due_61_90") or 0
                                ),
                            },
                            {
                                "metric": "Renewals due in 90 days",
                                "value": int(
                                    renewals.get("due_next_90") or 0
                                ),
                            },
                            {
                                "metric": "Overdue renewals",
                                "value": int(
                                    renewals.get("actionable_overdue") or 0
                                ),
                            },
                            {
                                "metric": "US subscriptions",
                                "value": int(
                                    renewals.get("us_subscriptions") or 0
                                ),
                            },
                            {
                                "metric": "Canada subscriptions",
                                "value": int(
                                    renewals.get("canada_subscriptions") or 0
                                ),
                            },
                            {
                                "metric": "Database refreshed",
                                "value": (
                                    renewals.get("last_refreshed") or "—"
                                ),
                                "value_type": "date",
                            },
                        ],
                    },
                    {
                        "title": "Ticket Details",
                        "columns": [
                            {
                                "key": "ticket_id",
                                "label": "Ticket ID",
                            },
                            {
                                "key": "customer",
                                "label": "Customer",
                            },
                            {
                                "key": "subject",
                                "label": "Subject",
                            },
                            {
                                "key": "status",
                                "label": "Status",
                            },
                            {
                                "key": "priority",
                                "label": "Priority",
                            },
                            {
                                "key": "engineer",
                                "label": "Engineer",
                            },
                            {
                                "key": "age_days",
                                "label": "Age",
                                "type": "days",
                            },
                        ],
                        "rows": [
                            {
                                "ticket_id": (
                                    ticket.get("ticket_id")
                                    or ticket.get("ticketId")
                                    or ticket.get("id")
                                    or "—"
                                ),
                                "customer": (
                                    ticket.get("customer_name")
                                    or ticket.get("customerName")
                                    or ticket.get("customer")
                                    or "—"
                                ),
                                "subject": ticket.get("subject") or "—",
                                "status": ticket.get("status") or "—",
                                "priority": ticket.get("priority") or "—",
                                "engineer": (
                                    ticket.get("assigned_engineer")
                                    or ticket.get("assignedEngineer")
                                    or "Unassigned"
                                ),
                                "age_days": ticket.get("age_days") or 0,
                            }
                            for ticket in (data.get("tickets") or [])[:200]
                        ],
                    },
                    {
                        "title": "Project Details",
                        "columns": [
                            {
                                "key": "project",
                                "label": "Project",
                            },
                            {
                                "key": "customer",
                                "label": "Customer",
                            },
                            {
                                "key": "status",
                                "label": "Status",
                            },
                            {
                                "key": "owner",
                                "label": "Owner",
                            },
                            {
                                "key": "start",
                                "label": "Start",
                                "type": "date",
                            },
                            {
                                "key": "end",
                                "label": "End",
                                "type": "date",
                            },
                        ],
                        "rows": [
                            {
                                "project": (
                                    project.get("projectName")
                                    or project.get("name")
                                    or project.get("title")
                                    or "—"
                                ),
                                "customer": (
                                    project.get("customerName")
                                    or project.get("customer_name")
                                    or project.get("customer")
                                    or "—"
                                ),
                                "status": (
                                    project.get("projectStatusName")
                                    or project.get("status")
                                    or "—"
                                ),
                                "owner": (
                                    project.get("projectManagerName")
                                    or project.get("projectManager")
                                    or project.get("ownerName")
                                    or project.get("owner")
                                    or "—"
                                ),
                                "start": (
                                    project.get("projectStartDate")
                                    or project.get("startDate")
                                    or project.get("start_date")
                                ),
                                "end": (
                                    project.get("projectEndDate")
                                    or project.get("endDate")
                                    or project.get("end_date")
                                ),
                            }
                            for project in (data.get("projects") or [])[:200]
                        ],
                    },
                    {
                        "title": "Opportunity Details",
                        "columns": [
                            {
                                "key": "opportunity",
                                "label": "Opportunity",
                            },
                            {
                                "key": "customer",
                                "label": "Customer",
                            },
                            {
                                "key": "stage",
                                "label": "Stage",
                            },
                            {
                                "key": "owner",
                                "label": "Owner",
                            },
                            {
                                "key": "amount",
                                "label": "Amount",
                                "type": "currency",
                            },
                            {
                                "key": "close_date",
                                "label": "Expected Close",
                                "type": "date",
                            },
                        ],
                        "rows": [
                            {
                                "opportunity": (
                                    opportunity.get("name")
                                    or opportunity.get("title")
                                    or opportunity.get("subject")
                                    or (
                                        f"Opportunity #"
                                        f"{opportunity.get('opportunityId') or opportunity.get('id') or '—'}"
                                    )
                                ),
                                "customer": (
                                    opportunity.get("customerName")
                                    or opportunity.get("customer")
                                    or opportunity.get("accountName")
                                    or "—"
                                ),
                                "stage": (
                                    opportunity.get("stageName")
                                    or opportunity.get("stage")
                                    or opportunity.get("status")
                                    or "—"
                                ),
                                "owner": (
                                    opportunity.get("ownerName")
                                    or opportunity.get("owner")
                                    or opportunity.get("assignedToName")
                                    or "—"
                                ),
                                "amount": _number_from_record(
                                    opportunity,
                                    (
                                        "amount",
                                        "Amount",
                                        "value",
                                        "Value",
                                        "estimatedValue",
                                        "estimated_value",
                                    ),
                                ),
                                "close_date": (
                                    opportunity.get("expectedCloseDate")
                                    or opportunity.get("closeDate")
                                    or opportunity.get("close_date")
                                ),
                            }
                            for opportunity in (
                                (data.get("opportunities") or [])
                                + (
                                    [data.get("opportunity")]
                                    if isinstance(
                                        data.get("opportunity"),
                                        dict,
                                    )
                                    else []
                                )
                            )[:200]
                        ],
                    },
                ],
                kpis=[
                    {
                        "label": "Health Score",
                        "value": company.get("score") or 0,
                        "format": "score",
                    },
                    {
                        "label": "Active Tickets",
                        "value": support.get("active_tickets") or 0,
                        "format": "number",
                    },
                    {
                        "label": "Active Projects",
                        "value": projects.get("active_projects") or 0,
                        "format": "number",
                    },
                    {
                        "label": "Pipeline",
                        "value": sales.get("pipeline_value") or 0,
                        "format": "currency",
                    },
                    {
                        "label": "Net Billing",
                        "value": net,
                        "format": "currency",
                    },
                    {
                        "label": "Renewals Due in 90 Days",
                        "value": renewals.get("due_next_90") or 0,
                        "format": "number",
                    },
                ],
            )
        )


    tickets = data.get("tickets")
    if (
        not isinstance(company, dict)
        and isinstance(tickets, list)
        and tickets
    ):
        ticket_rows = [
            {
                "ticket_id": (
                    ticket.get("ticket_id")
                    or ticket.get("ticketId")
                ),
                "customer": (
                    ticket.get("customer_name")
                    or ticket.get("customerName")
                    or "—"
                ),
                "subject": ticket.get("subject") or "—",
                "status": ticket.get("status") or "—",
                "priority": ticket.get("priority") or "—",
                "engineer": (
                    ticket.get("assigned_engineer") or "Unassigned"
                ),
                "age_days": ticket.get("age_days") or 0,
            }
            for ticket in tickets[:200]
        ]
        needs_review = sum(
            1
            for ticket in tickets
            if "review" in str(ticket.get("status") or "").casefold()
        )
        reports.append(
            _standard_report(
                report_type="ticket_summary",
                title="Support Ticket Report",
                source="Rev.io PSA",
                summary=(
                    f"{len(tickets)} tickets returned; "
                    f"{needs_review} need review."
                ),
                kpis=[
                    {
                        "label": "Tickets",
                        "value": len(tickets),
                        "format": "number",
                    },
                    {
                        "label": "Needs Review",
                        "value": needs_review,
                        "format": "number",
                    },
                ],
                columns=[
                    {"key": "ticket_id", "label": "Ticket ID"},
                    {"key": "customer", "label": "Customer"},
                    {"key": "subject", "label": "Subject"},
                    {"key": "status", "label": "Status"},
                    {"key": "priority", "label": "Priority"},
                    {"key": "engineer", "label": "Engineer"},
                    {
                        "key": "age_days",
                        "label": "Age",
                        "type": "days",
                    },
                ],
                rows=ticket_rows,
                detail_title="Ticket Details",
            )
        )

    service_products = data.get("service_products")
    if isinstance(service_products, list):
        customer_name = str(data.get("customer_name") or "Customer")
        license_quantity = float(data.get("license_quantity") or 0)
        other_service_quantity = float(
            data.get("other_service_quantity") or 0
        )

        rows = []
        for row in data.get("license_breakdown") or []:
            rows.append(
                {
                    "category": "License",
                    "service": row.get("description"),
                    "quantity": row.get("quantity"),
                }
            )
        for row in data.get("other_service_breakdown") or []:
            rows.append(
                {
                    "category": "Other active service",
                    "service": row.get("description"),
                    "quantity": row.get("quantity"),
                }
            )

        reports.append(
            _standard_report(
                report_type="service_products",
                title=f"{customer_name} Service Products",
                source="Rev.io Billing",
                summary=(
                    f"{customer_name} has "
                    f"{int(license_quantity) if license_quantity.is_integer() else license_quantity} "
                    "current user/agent licenses."
                ),
                kpis=[
                    {
                        "label": "Current Licenses",
                        "value": license_quantity,
                        "format": "number",
                    },
                    {
                        "label": "Other Active Service Units",
                        "value": other_service_quantity,
                        "format": "number",
                    },
                    {
                        "label": "Active Line Items",
                        "value": len(service_products),
                        "format": "number",
                    },
                ],
                columns=[
                    {"key": "category", "label": "Category"},
                    {"key": "service", "label": "Service"},
                    {"key": "quantity", "label": "Quantity"},
                ],
                rows=rows,
                detail_title="Active Services",
            )
        )

    projects = data.get("projects")
    if (
        not isinstance(company, dict)
        and isinstance(projects, list)
        and projects
    ):
        project_rows = [
            {
                "project": (
                    item.get("projectName")
                    or item.get("name")
                    or "—"
                ),
                "customer": (
                    item.get("customerName")
                    or item.get("customer_name")
                    or "—"
                ),
                "status": (
                    item.get("projectStatusName")
                    or item.get("status")
                    or "—"
                ),
                "owner": (
                    item.get("projectManagerName")
                    or item.get("ownerName")
                    or "—"
                ),
                "start": item.get("startDate"),
                "end": item.get("endDate"),
            }
            for item in projects[:200]
        ]
        reports.append(
            _standard_report(
                report_type="project_summary",
                title="Project Report",
                source="Rev.io PSA",
                summary=f"{len(projects)} projects returned.",
                kpis=[
                    {
                        "label": "Projects",
                        "value": len(projects),
                        "format": "number",
                    }
                ],
                columns=[
                    {"key": "project", "label": "Project"},
                    {"key": "customer", "label": "Customer"},
                    {"key": "status", "label": "Status"},
                    {"key": "owner", "label": "Owner"},
                    {"key": "start", "label": "Start", "type": "date"},
                    {"key": "end", "label": "End", "type": "date"},
                ],
                rows=project_rows,
                detail_title="Project Details",
            )
        )

    opportunities = data.get("opportunities")
    if (
        not isinstance(company, dict)
        and isinstance(opportunities, list)
        and opportunities
    ):
        opportunity_rows = []
        pipeline = 0.0
        for item in opportunities[:200]:
            amount = _number_from_record(
                item,
                ("amount", "Amount", "value", "estimatedValue"),
            )
            pipeline += amount
            opportunity_rows.append(
                {
                    "opportunity": (
                        item.get("name")
                        or item.get("title")
                        or "—"
                    ),
                    "customer": (
                        item.get("customerName")
                        or item.get("customer")
                        or "—"
                    ),
                    "stage": (
                        item.get("stageName")
                        or item.get("status")
                        or "—"
                    ),
                    "owner": (
                        item.get("ownerName")
                        or item.get("owner")
                        or "—"
                    ),
                    "amount": amount,
                    "close_date": (
                        item.get("expectedCloseDate")
                        or item.get("closeDate")
                    ),
                }
            )
        reports.append(
            _standard_report(
                report_type="opportunity_summary",
                title="Sales Opportunity Report",
                source="Rev.io PSA",
                summary=(
                    f"{len(opportunities)} opportunities represent "
                    f"${pipeline:,.2f} in pipeline."
                ),
                kpis=[
                    {
                        "label": "Opportunities",
                        "value": len(opportunities),
                        "format": "number",
                    },
                    {
                        "label": "Pipeline",
                        "value": pipeline,
                        "format": "currency",
                    },
                ],
                columns=[
                    {"key": "opportunity", "label": "Opportunity"},
                    {"key": "customer", "label": "Customer"},
                    {"key": "stage", "label": "Stage"},
                    {"key": "owner", "label": "Owner"},
                    {
                        "key": "amount",
                        "label": "Amount",
                        "type": "currency",
                    },
                    {
                        "key": "close_date",
                        "label": "Expected Close",
                        "type": "date",
                    },
                ],
                rows=opportunity_rows,
                detail_title="Opportunity Details",
            )
        )

    return reports


def _records_from_response(
    value: Any,
    *,
    preferred_keys: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [
            item
            for item in value
            if isinstance(item, dict)
        ]

    if not isinstance(value, dict):
        return []

    candidate_keys = (
        *preferred_keys,
        "data",
        "items",
        "results",
        "records",
        "objects",
        "customers",
        "serviceProducts",
        "service_products",
        "products",
        "projects",
        "opportunities",
        "tickets",
    )

    for key in candidate_keys:
        candidate = value.get(key)
        if isinstance(candidate, list):
            return [
                item
                for item in candidate
                if isinstance(item, dict)
            ]
        if isinstance(candidate, dict):
            nested = _records_from_response(candidate)
            if nested:
                return nested

    return []


def _is_active_service_product(
    product: dict[str, Any],
) -> bool:
    status = _status_value(
        product,
        (
            "Status",
            "status",
            "ServiceStatus",
            "serviceStatus",
            "serviceStatusName",
            "statusName",
            "productStatus",
            "productStatusName",
            "state",
        ),
    ).casefold().strip()

    inactive_values = {
        "inactive",
        "disabled",
        "cancelled",
        "canceled",
        "terminated",
        "expired",
        "deleted",
        "disconnected",
        "suspended",
    }
    active_values = {
        "active",
        "enabled",
        "current",
        "live",
        "provisioned",
        "installed",
        "open",
    }

    if status in active_values:
        return True
    if status in inactive_values:
        return False

    for key in (
        "Active",
        "active",
        "IsActive",
        "isActive",
        "Enabled",
        "enabled",
    ):
        value = product.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.casefold().strip()
            if normalized in {"true", "yes", "1"}:
                return True
            if normalized in {"false", "no", "0"}:
                return False

    end_value = None
    for key in (
        "DisconnectDate",
        "disconnectDate",
        "TerminationDate",
        "terminationDate",
        "EndDate",
        "endDate",
        "CancelledDate",
        "cancelledDate",
    ):
        if product.get(key) not in (None, ""):
            end_value = product.get(key)
            break

    return status == "unknown" and end_value in (None, "")


def _number_from_record(
    record: dict[str, Any],
    keys: tuple[str, ...],
) -> float:
    for key in keys:
        value = record.get(key)
        if value in (None, ""):
            continue
        try:
            return float(
                str(value)
                .replace("$", "")
                .replace(",", "")
            )
        except (TypeError, ValueError):
            continue
    return 0.0


def _status_value(
    record: dict[str, Any],
    keys: tuple[str, ...],
) -> str:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return "Unknown"


def _company_health_score(
    *,
    active_tickets: int,
    needs_review: int,
    oldest_ticket_days: int,
    actionable_overdue: int,
    due_30: int,
    active_projects: int,
    project_count: int,
    opportunity_count: int,
) -> tuple[int, str]:
    score = 100
    score -= min(needs_review * 5, 20)
    score -= min(actionable_overdue * 12, 36)
    score -= min(due_30 * 2, 16)

    if oldest_ticket_days >= 30:
        score -= 15
    elif oldest_ticket_days >= 14:
        score -= 8
    elif oldest_ticket_days >= 7:
        score -= 4

    if active_tickets >= 75:
        score -= 10
    elif active_tickets >= 40:
        score -= 5

    if project_count and active_projects == 0:
        score -= 5

    if opportunity_count == 0:
        score -= 5

    score = max(min(score, 100), 0)

    if score >= 85:
        rating = "Strong"
    elif score >= 70:
        rating = "Good"
    elif score >= 50:
        rating = "Needs Attention"
    else:
        rating = "At Risk"

    return score, rating


def _project_status(project: dict[str, Any]) -> str:
    return _status_value(
        project,
        (
            "projectStatusName",
            "statusName",
            "status",
            "projectStatus",
            "state",
        ),
    )


def _build_company_snapshot(
    *,
    period_days: int,
    tickets: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    billing_customers: list[dict[str, Any]],
    billing_ledger: dict[str, Any],
    renewals: list[dict[str, Any]],
    renewal_summary: dict[str, Any],
    errors: dict[str, str],
) -> dict[str, Any]:
    active_tickets = active_only(tickets)
    ticket_statuses: dict[str, int] = {}
    engineer_counts: dict[str, int] = {}

    for ticket in active_tickets:
        status = str(ticket.get("status") or "Unknown")
        ticket_statuses[status] = ticket_statuses.get(status, 0) + 1
        engineer = str(
            ticket.get("assigned_engineer")
            or "Unassigned"
        )
        engineer_counts[engineer] = (
            engineer_counts.get(engineer, 0) + 1
        )

    oldest_ticket_days = max(
        (
            int(ticket.get("age_days") or 0)
            for ticket in active_tickets
        ),
        default=0,
    )
    average_ticket_age = (
        sum(
            int(ticket.get("age_days") or 0)
            for ticket in active_tickets
        )
        / len(active_tickets)
        if active_tickets
        else 0
    )
    needs_review = sum(
        count
        for status, count in ticket_statuses.items()
        if status.casefold() == "needs reviewed"
    )
    on_hold = sum(
        count
        for status, count in ticket_statuses.items()
        if status.casefold() in {"on-hold", "on hold"}
    )
    top_engineer = (
        max(
            engineer_counts.items(),
            key=lambda item: item[1],
        )
        if engineer_counts
        else ("None", 0)
    )

    active_project_statuses = {
        "active",
        "open",
        "in progress",
        "in-progress",
    }
    active_projects = [
        project
        for project in projects
        if _project_status(project).casefold()
        in active_project_statuses
    ]
    project_statuses: dict[str, int] = {}
    for project in projects:
        status = _project_status(project)
        project_statuses[status] = (
            project_statuses.get(status, 0) + 1
        )

    pipeline_value = sum(
        _number_from_record(
            opportunity,
            (
                "amount",
                "Amount",
                "value",
                "Value",
                "estimatedValue",
                "opportunityValue",
            ),
        )
        for opportunity in opportunities
    )
    opportunity_stages: dict[str, int] = {}
    for opportunity in opportunities:
        stage = _status_value(
            opportunity,
            (
                "stageName",
                "opportunityStageName",
                "status",
                "Status",
                "stage",
            ),
        )
        opportunity_stages[stage] = (
            opportunity_stages.get(stage, 0) + 1
        )

    ledger_total_charges = float(
        billing_ledger.get("total_charges") or 0
    )
    ledger_total_credits = float(
        billing_ledger.get("total_credits") or 0
    )
    ledger_net_activity = float(
        billing_ledger.get(
            "net_charges_less_credits"
        )
        or (ledger_total_charges - ledger_total_credits)
    )
    ledger_charge_count = int(
        billing_ledger.get("charge_count") or 0
    )
    ledger_credit_count = int(
        billing_ledger.get("credit_count") or 0
    )
    customers_with_activity = int(
        billing_ledger.get(
            "customers_with_activity"
        )
        or 0
    )

    renewal_market_counts: dict[str, int] = dict(
        renewal_summary.get("market_counts") or {}
    )
    for renewal in renewals:
        raw_market = str(
            renewal.get("market")
            or renewal.get("country")
            or "Unknown"
        ).strip()
        market_key = (
            "Canada"
            if raw_market.casefold()
            in {"canada", "ca", "can"}
            else "US"
            if raw_market.casefold()
            in {
                "us",
                "usa",
                "united states",
                "united states of america",
            }
            else raw_market or "Unknown"
        )
        renewal_market_counts[market_key] = (
            renewal_market_counts.get(market_key, 0) + 1
        )

    actionable_overdue = int(
        renewal_summary.get("actionable_overdue") or 0
    )
    due_30 = int(renewal_summary.get("due_0_30") or 0)
    due_31_60 = int(
        renewal_summary.get("due_31_60") or 0
    )
    due_61_90 = int(
        renewal_summary.get("due_61_90") or 0
    )

    score, rating = _company_health_score(
        active_tickets=len(active_tickets),
        needs_review=needs_review,
        oldest_ticket_days=oldest_ticket_days,
        actionable_overdue=actionable_overdue,
        due_30=due_30,
        active_projects=len(active_projects),
        project_count=len(projects),
        opportunity_count=len(opportunities),
    )

    attention_items: list[dict[str, Any]] = []

    if actionable_overdue:
        attention_items.append(
            {
                "severity": "Critical",
                "area": "Renewals",
                "title": (
                    f"{actionable_overdue} active Cisco "
                    "renewals are overdue"
                ),
                "detail": (
                    "These subscriptions have passed their "
                    "renewal date and remain active."
                ),
            }
        )

    if due_30:
        attention_items.append(
            {
                "severity": "High",
                "area": "Renewals",
                "title": (
                    f"{due_30} active Cisco renewals are due "
                    "within 30 days"
                ),
                "detail": (
                    "Prioritize customer outreach and quoting."
                ),
            }
        )

    if needs_review:
        attention_items.append(
            {
                "severity": "High",
                "area": "Support",
                "title": (
                    f"{needs_review} tickets need review"
                ),
                "detail": (
                    "Review ownership, next action, and customer "
                    "communication."
                ),
            }
        )

    if on_hold:
        attention_items.append(
            {
                "severity": "Medium",
                "area": "Support",
                "title": f"{on_hold} tickets are on hold",
                "detail": (
                    "Confirm dependencies and next follow-up dates."
                ),
            }
        )

    if oldest_ticket_days >= 14:
        attention_items.append(
            {
                "severity": (
                    "High"
                    if oldest_ticket_days >= 30
                    else "Medium"
                ),
                "area": "Support",
                "title": (
                    f"The oldest active ticket is "
                    f"{oldest_ticket_days} days old"
                ),
                "detail": (
                    "Review aging tickets for escalation or closure."
                ),
            }
        )

    if not opportunities:
        attention_items.append(
            {
                "severity": "Medium",
                "area": "Sales",
                "title": "No open opportunities were returned",
                "detail": (
                    "Confirm that pipeline records are current in Rev.io."
                ),
            }
        )

    for system_name, error in errors.items():
        attention_items.append(
            {
                "severity": "Review",
                "area": "Integration",
                "title": f"{system_name} data was unavailable",
                "detail": error[:300],
            }
        )

    return {
        "company_health": {
            "score": score,
            "rating": rating,
            "period_days": period_days,
            "systems_available": (
                5 - len(errors)
            ),
            "systems_expected": 5,
        },
        "support_health": {
            "active_tickets": len(active_tickets),
            "new_tickets": ticket_statuses.get("New", 0),
            "open_tickets": ticket_statuses.get("Open", 0),
            "on_hold": on_hold,
            "needs_review": needs_review,
            "average_age_days": round(
                average_ticket_age,
                1,
            ),
            "oldest_age_days": oldest_ticket_days,
            "engineers": len(engineer_counts),
            "highest_workload_engineer": top_engineer[0],
            "highest_workload_count": top_engineer[1],
            "status_counts": ticket_statuses,
            "engineer_counts": engineer_counts,
        },
        "project_health": {
            "total_projects": len(projects),
            "active_projects": len(active_projects),
            "status_counts": project_statuses,
        },
        "sales_health": {
            "open_opportunities": len(opportunities),
            "pipeline_value": round(pipeline_value, 2),
            "stage_counts": opportunity_stages,
        },
        "billing_health": {
            "period_days": period_days,
            "customers_returned": len(billing_customers),
            "customers_with_activity": (
                customers_with_activity
            ),
            "charge_count": ledger_charge_count,
            "credit_count": ledger_credit_count,
            "total_charges": round(
                ledger_total_charges,
                2,
            ),
            "total_credits": round(
                ledger_total_credits,
                2,
            ),
            "net_billing_activity": round(
                ledger_net_activity,
                2,
            ),
            "payment_data_included": bool(
                billing_ledger.get(
                    "payment_data_included"
                )
            ),
            "ledger_source": billing_ledger.get(
                "ledger_source",
                "Rev.io Billing ledger",
            ),
            "date_start": billing_ledger.get(
                "created_date_start"
            ),
            "date_end": billing_ledger.get(
                "created_date_end"
            ),
        },
        "renewal_health": {
            **renewal_summary,
            "due_next_90": (
                due_30 + due_31_60 + due_61_90
            ),
            "market_counts": renewal_market_counts,
            "us_subscriptions": renewal_market_counts.get(
                "US",
                0,
            ),
            "canada_subscriptions": renewal_market_counts.get(
                "Canada",
                0,
            ),
            "markets_returned": (
                renewal_summary.get("markets_returned")
                or sorted(
                    market
                    for market, count in renewal_market_counts.items()
                    if count
                )
            ),
            "total_subscriptions": int(
                renewal_summary.get("total_subscriptions")
                or len(renewals)
            ),
            "last_refreshed": renewal_summary.get("last_refreshed"),
            "source": renewal_summary.get("source", "Ribbit PostgreSQL"),
        },
        "attention_items": attention_items,
        "tickets": active_tickets,
        "projects": projects,
        "opportunities": opportunities,
        "customers": billing_customers,
        "ccwr_renewals": renewals,
        "source": "Bullfrog Company Health",
        "integration_errors": errors,
    }


def _compact_company_snapshot(
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "company_health": data.get("company_health"),
        "support_health": data.get("support_health"),
        "project_health": data.get("project_health"),
        "sales_health": data.get("sales_health"),
        "billing_health": data.get("billing_health"),
        "renewal_health": data.get("renewal_health"),
        "attention_items": data.get("attention_items"),
        "integration_errors": data.get(
            "integration_errors"
        ),
        "sample_tickets": (
            data.get("tickets") or []
        )[:10],
        "sample_projects": (
            data.get("projects") or []
        )[:5],
        "sample_opportunities": (
            data.get("opportunities") or []
        )[:5],
        "sample_renewals": (
            data.get("ccwr_renewals") or []
        )[:10],
        "source": data.get("source"),
    }



def _service_product_description(product: dict[str, Any]) -> str:
    value = (
        product.get("description")
        or product.get("Description")
        or product.get("wholesale_description")
        or product.get("WholesaleDescription")
        or ""
    )
    return str(value).strip()


def _is_license_service_product(product: dict[str, Any]) -> bool:
    """
    Classify a Rev.io service-product row as a user/agent/license quantity.

    This intentionally excludes phone numbers, call paths, recording products,
    texting, hardware, usage, and similar non-seat billing items.
    """
    description = _service_product_description(product).casefold()

    include_terms = (
        "professional",
        "pro user",
        "premium agent",
        "standard agent",
        "contact center agent",
        "webex calling",
        "calling pro",
        "calling professional",
        "calling enhanced",
        "enhanced user",
        "common area",
        "workspace",
        "user license",
        "agent license",
        "suite",
        "customer assist",
    )

    exclude_terms = (
        "telephone number",
        "phone number",
        "did",
        "toll-free",
        "toll free",
        "call path",
        "recording",
        "callcabinet",
        "texting",
        "sms",
        "mms",
        "hardware",
        "phone rental",
        "device",
        "usage",
        "regulatory",
        "fee",
        "tax",
        "ivr",
        "fax",
        "e911",
        "911",
    )

    if any(term in description for term in exclude_terms):
        return False

    return any(term in description for term in include_terms)


def _group_service_product_quantities(
    products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, float]:
    license_totals: dict[str, float] = {}
    other_totals: dict[str, float] = {}

    for product in products:
        quantity = _number_from_record(
            product,
            ("quantity", "Quantity", "qty", "Qty"),
        )
        description = _service_product_description(product) or "Unnamed service"

        target = (
            license_totals
            if _is_license_service_product(product)
            else other_totals
        )
        target[description] = target.get(description, 0.0) + quantity

    def rows(values: dict[str, float]) -> list[dict[str, Any]]:
        return [
            {"description": description, "quantity": quantity}
            for description, quantity in sorted(
                values.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        ]

    license_rows = rows(license_totals)
    other_rows = rows(other_totals)

    return (
        license_rows,
        other_rows,
        sum(license_totals.values()),
        sum(other_totals.values()),
    )


def _fallback_summary(
    operation: str,
    data: dict[str, Any],
    error: Exception,
) -> str:
    if operation == "ccwr_search_renewals":
        summary = data.get("renewal_summary") or {}
        count = int(summary.get("total_subscriptions") or 0)
        return (
            f"Cisco CCW-R returned {count} subscriptions.\n\n"
            f"• Active: {int(summary.get('active') or 0):,}\n"
            f"• Actionable overdue: "
            f"{int(summary.get('actionable_overdue') or 0):,}\n"
            f"• Due in 0-30 days: "
            f"{int(summary.get('due_0_30') or 0):,}\n"
            f"• Due in 31-60 days: "
            f"{int(summary.get('due_31_60') or 0):,}\n"
            f"• Due in 61-90 days: "
            f"{int(summary.get('due_61_90') or 0):,}\n"
            f"• Closed: {int(summary.get('closed') or 0):,}\n\n"
            "The complete renewal records are displayed below and saved "
            "for reporting."
        )

    if operation == "revio_billing_search_service_products":
        customer_name = (
            data.get("customer_name")
            or f"customer {data.get('customer_id', '')}".strip()
            or "the customer"
        )
        products = data.get("service_products") or []
        active_only_requested = bool(data.get("active_only"))

        license_breakdown = data.get("license_breakdown") or []
        other_breakdown = data.get("other_service_breakdown") or []
        license_quantity = float(data.get("license_quantity") or 0)

        def format_quantity(value: Any) -> str:
            quantity = float(value or 0)
            return (
                str(int(quantity))
                if quantity.is_integer()
                else f"{quantity:,.2f}"
            )

        scope = "currently has" if active_only_requested else "has"
        answer = (
            f"{customer_name} {scope} "
            f"{format_quantity(license_quantity)} user/agent licenses."
        )

        if license_breakdown:
            answer += "\n\nLicenses:\n"
            answer += "\n".join(
                f"• {row.get('description') or 'Unnamed license'}: "
                f"{format_quantity(row.get('quantity'))}"
                for row in license_breakdown[:20]
            )

        if other_breakdown:
            answer += "\n\nOther active services:\n"
            answer += "\n".join(
                f"• {row.get('description') or 'Unnamed service'}: "
                f"{format_quantity(row.get('quantity'))}"
                for row in other_breakdown[:20]
            )

        answer += (
            "\n\nCalculated directly from Rev.io Billing."
        )
        return answer

    if operation == "revio_billing_customer_ledger":
        summary = data.get("ledger_summary") or {}
        customer_name = (
            data.get("customer_name")
            or f"customer {data.get('customer_id', '')}".strip()
            or "the customer"
        )
        count = int(data.get("count") or 0)
        total_charges = float(summary.get("total_charges") or 0)
        total_credits = float(summary.get("total_credits") or 0)
        net = float(
            summary.get("net_charges_less_credits") or 0
        )
        ledger_source = (
            summary.get("ledger_source") or "not reported"
        )

        return (
            f"Rev.io returned {count} billing ledger entries for "
            f"{customer_name}.\n\n"
            f"• Total charges: ${total_charges:,.2f}\n"
            f"• Total credits: ${total_credits:,.2f}\n"
            f"• Charges less credits: ${net:,.2f}\n"
            f"• Ledger source: {ledger_source}\n\n"
            "The complete transaction records are still displayed below and "
            "remain available for PDF export. OpenAI could not format the "
            "narrative summary after multiple retries."
        )

    records = int(data.get("count") or 0)
    return (
        f"Rev.io returned {records} records. The structured results are "
        "available below, but OpenAI could not format the narrative summary "
        "after multiple retries."
    )


async def summarize(
    question: str,
    operation: str,
    data: dict[str, Any],
) -> str:
    if operation == "revio_billing_customer_ledger":
        summary_data = _compact_ledger_for_summary(data)
    elif operation == "ccwr_search_renewals":
        summary_data = _compact_ccwr_for_summary(data)
    elif operation == "company_health_snapshot":
        summary_data = _compact_company_snapshot(data)
    else:
        summary_data = data

    serialized = json.dumps(
        summary_data,
        ensure_ascii=False,
        default=str,
    )

    # General calls remain capped, while ledger calls are already reduced to
    # totals plus the most recent 30 entries.
    max_serialized_chars = (
        30_000
        if operation in {
            "revio_billing_customer_ledger",
            "ccwr_search_renewals",
            "company_health_snapshot",
        }
        else 70_000
    )
    if len(serialized) > max_serialized_chars:
        serialized = (
            serialized[:max_serialized_chars]
            + "\n[summary payload truncated]"
        )

    try:
        return await openai_response(
            instructions=SUMMARY_INSTRUCTIONS,
            input_text=(
                f"Question:\n{question}\n\n"
                f"Operation:\n{operation}\n\n"
                f"Live data:\n{serialized}"
            ),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 429:
            raise
        return _fallback_summary(operation, data, exc)


def active_only(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        ticket
        for ticket in tickets
        if str(ticket.get("status", "")).strip().casefold()
        in ACTIVE_REVIO_STATUSES
    ]


def _conversation_id(request: ChatRequest) -> str:
    return request.conversation_id or str(uuid.uuid4())


def _context_for_planner(conversation: dict[str, Any] | None) -> str:
    if not conversation:
        return "No previous conversation context."

    context_data = conversation.get("data") or {}
    serialized = json.dumps(context_data, ensure_ascii=False, default=str)
    if len(serialized) > 18_000:
        serialized = serialized[:18_000] + "\n[truncated]"

    return (
        f"Previous user question:\n{conversation.get('question', '')}\n\n"
        f"Previous assistant answer:\n{conversation.get('answer', '')}\n\n"
        f"Previous intent:\n{conversation.get('intent', '')}\n\n"
        f"Previous structured data:\n{serialized}"
    )


def _save_conversation(
    conversation_id: str,
    *,
    question: str,
    answer: str,
    intent: str,
    data: dict[str, Any],
) -> None:
    dataset = save_dataset(
        conversation_id=conversation_id,
        query=question,
        intent=intent,
        data=data,
    )
    if dataset:
        data["dataset_id"] = dataset["dataset_id"]
        data["dataset_title"] = dataset["title"]
        data["dataset_type"] = dataset["data_type"]

    previous = CONVERSATIONS.get(conversation_id) or {}
    history = list(previous.get("history") or [])
    history.append(
        {
            "question": question,
            "answer": answer,
            "intent": intent,
            "dataset_id": dataset.get("dataset_id") if dataset else None,
        }
    )

    CONVERSATIONS[conversation_id] = {
        "question": question,
        "answer": answer,
        "intent": intent,
        "data": deepcopy(data),
        "history": history[-50:],
        "datasets": list_conversation_dataset_summaries(conversation_id),
    }


def _looks_like_pdf_followup(message: str, has_previous_data: bool) -> bool:
    if not has_previous_data:
        return False

    normalized = re.sub(r"\s+", " ", message.strip().casefold())
    direct = (
        "pdf" in normalized
        and any(
            word in normalized
            for word in ("make", "create", "generate", "download", "export", "yes")
        )
    )
    confirmation = normalized in {
        "yes",
        "yes please",
        "please do",
        "do it",
        "make it",
        "create it",
        "that would be great",
        "sure",
    }
    return direct or confirmation



def _fallback_plan_from_message(message: str) -> dict[str, Any]:
    """
    Deterministic planner used only when OpenAI planning is temporarily
    unavailable, especially during HTTP 429 rate limiting.
    """
    text = " ".join(message.strip().split())
    lowered = text.casefold()

    # Use a punctuation-normalized form for deterministic regex routing.
    # This keeps natural questions such as "...currently?" from accidentally
    # falling through to general_chat and requiring the OpenAI planner.
    route_text = text.rstrip(" .?!")
    route_lowered = route_text.casefold()

    def after_any(markers: tuple[str, ...]) -> str | None:
        for marker in markers:
            pos = lowered.find(marker)
            if pos >= 0:
                value = text[pos + len(marker):].strip(" .?!")
                return value or None
        return None

    if (
        "how is my company" in lowered
        or "how my company is doing" in lowered
        or "company health" in lowered
        or "executive dashboard" in lowered
        or "executive snapshot" in lowered
        or "what needs my attention" in lowered
    ):
        return {
            "operation": "company_health_snapshot",
            "arguments": {
                "period_days": 30,
                "ccwr_lookback_days": 180,
                "market": "All",
            },
        }

    if (
        "customer 360" in lowered
        or "account 360" in lowered
        or "everything about" in lowered
        or "customer overview" in lowered
        or "account overview" in lowered
    ):
        customer_name = after_any(
            (
                "everything about ",
                "customer 360 for ",
                "account 360 for ",
                "customer overview for ",
                "account overview for ",
            )
        )
        return {
            "operation": "customer_360",
            "arguments": {
                "customer_name": customer_name,
                "period_days": 90,
            },
        }

    if "renewal" in lowered or "ccw-r" in lowered or "ccwr" in lowered:
        market = "All"
        if "canada" in lowered and not any(
            token in lowered
            for token in (" us ", " usa ", "united states", "both")
        ):
            market = "Canada"
        elif (
            "united states" in lowered
            or " usa " in f" {lowered} "
            or " us " in f" {lowered} "
        ) and "canada" not in lowered:
            market = "US"

        scope = "all"
        if "overdue" in lowered or "past due" in lowered:
            scope = "past_due"
        elif "30 day" in lowered or "30-day" in lowered:
            scope = "next_30"
        elif "60 day" in lowered or "60-day" in lowered:
            scope = "next_60"
        elif "90 day" in lowered or "90-day" in lowered or "upcoming" in lowered:
            scope = "next_90"
        elif "180 day" in lowered or "180-day" in lowered:
            scope = "next_180"

        return {
            "operation": "ccwr_search_renewals",
            "arguments": {
                "market": market,
                "renewal_scope": scope,
                "active_only": "active" in lowered,
            },
        }

    # License / seat / current-service questions are deterministic business-data
    # lookups and should not depend on the OpenAI planner being available.
    license_terms = (
        "license",
        "licenses",
        "seat",
        "seats",
        "current services",
        "currently has",
        "active services",
        "service products",
        "subscriptions",
    )

    if any(term in route_lowered for term in license_terms):
        customer_name: str | None = None

        patterns = (
            # How many licenses does Acme have currently
            r"how many\s+(?:licenses?|seats?|users?)\s+does\s+(.+?)\s+have(?:\s+currently|\s+right\s+now|\s+today)?$",

            # How many licenses does Acme currently have
            r"how many\s+(?:licenses?|seats?|users?)\s+does\s+(.+?)\s+(?:currently\s+|right\s+now\s+)?have$",

            # What licenses/services does Acme have currently
            r"what\s+(?:licenses?|services|products|subscriptions)\s+does\s+(.+?)\s+have(?:\s+currently|\s+right\s+now|\s+today)?$",

            # What licenses/services does Acme currently have
            r"what\s+(?:licenses?|services|products|subscriptions)\s+does\s+(.+?)\s+(?:currently\s+|right\s+now\s+)?have$",

            # Licenses for Acme / active services for Acme
            r"(?:licenses?|seats?|current services|active services|service products|subscriptions)\s+(?:for|at|of)\s+(.+)$",

            # How many licenses for Acme
            r"how many\s+(?:licenses?|seats?|users?)\s+(?:for|at|of)\s+(.+)$",

            # Does Acme have X licenses / what does Acme have for licenses
            r"what\s+does\s+(.+?)\s+have\s+for\s+(?:licenses?|seats?|services|subscriptions)$",
        )

        for pattern in patterns:
            match = re.search(pattern, route_text, flags=re.IGNORECASE)
            if match:
                customer_name = match.group(1).strip(" .?!")
                break

        if customer_name:
            return {
                "operation": "revio_billing_search_service_products",
                "arguments": {
                    "customer_name": customer_name,
                    "active_only": True,
                    "page": 1,
                    "page_size": 500,
                },
            }

        # License wording was detected but no exact pattern matched. Try a
        # conservative extraction around "does <customer> ... have" before
        # ever falling back to OpenAI.
        does_match = re.search(
            r"\bdoes\s+(.+?)\s+(?:(?:currently|right\s+now)\s+)?have\b",
            route_text,
            flags=re.IGNORECASE,
        )
        if does_match:
            customer_name = does_match.group(1).strip(" .?!")
            if customer_name:
                return {
                    "operation": "revio_billing_search_service_products",
                    "arguments": {
                        "customer_name": customer_name,
                        "active_only": True,
                        "page": 1,
                        "page_size": 500,
                    },
                }

    if "contact" in lowered:
        customer_name = after_any(
            (
                "contacts for ",
                "contact for ",
                "contacts at ",
                "contact at ",
                "contacts from ",
                "contact from ",
                "contacts of ",
                "contact of ",
            )
        )
        if customer_name:
            return {
                "operation": "revio_billing_search_contacts",
                "arguments": {
                    "customer_name": customer_name,
                    "query": customer_name,
                    "page": 1,
                    "page_size": 100,
                },
            }

    if "ticket" in lowered:
        return {
            "operation": "revio_search_tickets",
            "arguments": {
                "active_only": "active" in lowered or "open" in lowered,
                "page_size": 500,
            },
        }

    if "project" in lowered:
        return {
            "operation": "revio_search_projects",
            "arguments": {
                "query": after_any(("projects for ", "project for ")),
                "page": 1,
                "per_page": 100,
            },
        }

    if "opportunit" in lowered or "pipeline" in lowered:
        return {
            "operation": "revio_search_opportunities",
            "arguments": {
                "page": 1,
                "per_page": 100,
            },
        }

    return {
        "operation": "general_chat",
        "arguments": {},
    }


def _openai_rate_limited(exc: Exception) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response is not None
        and exc.response.status_code == 429
    )


async def handle_chat(request: ChatRequest) -> ChatResponse:
    conversation_id = _conversation_id(request)
    previous = CONVERSATIONS.get(conversation_id)
    previous_data = previous.get("data") if previous else None

    if _looks_like_pdf_followup(request.message, bool(previous_data)):
        operation = "generate_previous_results_pdf"
        args: dict[str, Any] = {}
    else:
        # Route obvious structured business-data requests locally first.
        # This avoids spending an OpenAI planner request on deterministic
        # intents and keeps live lookups working during API 429s.
        deterministic_plan = _fallback_plan_from_message(request.message)

        if deterministic_plan.get("operation") != "general_chat":
            plan = deterministic_plan
        else:
            planner_input = (
                f"Current user message:\n{request.message}\n\n"
                f"{_context_for_planner(previous)}"
            )
            try:
                plan = extract_json(
                    await openai_response(
                        instructions=PLANNER_INSTRUCTIONS,
                        input_text=planner_input,
                        max_output_tokens=350,
                    )
                )
            except Exception as exc:
                if not _openai_rate_limited(exc):
                    raise
                plan = deterministic_plan

        operation = str(plan.get("operation") or "general_chat")
        args = (
            plan.get("arguments")
            if isinstance(plan.get("arguments"), dict)
            else {}
        )

    require_permission(request.user, "tickets")

    data: dict[str, Any]
    intent = operation
    label = "Live Rev.io PSA data interpreted by OpenAI"

    if operation == "generate_previous_results_pdf":
        if not previous or not previous.get("data"):
            answer = (
                "There are no previous structured results in this conversation "
                "to export yet. Run a ticket, project, invoice, customer, contact, "
                "or opportunity query first."
            )
            return ChatResponse(
                answer=answer,
                intent=operation,
                conversation_id=conversation_id,
                sources=[],
            )

        path, filename = create_pdf_report(
            conversation_id=conversation_id,
            answer=str(previous.get("answer") or ""),
            intent=str(previous.get("intent") or "report"),
            data=deepcopy(previous["data"]),
        )
        answer = f"Your PDF is ready: {filename}"
        return ChatResponse(
            answer=answer,
            intent=operation,
            conversation_id=conversation_id,
            data=deepcopy(previous["data"]),
            download_url=f"/api/downloads/{path.name}",
            download_name=filename,
            sources=[
                SourceReference(
                    system="Bullfrog Intelligence",
                    label="PDF generated from the previous live Rev.io results",
                )
            ],
        )

    if operation == "customer_360":
        customer_name = str(
            args.get("customer_name") or ""
        ).strip()

        if not customer_name:
            answer = (
                "Which customer would you like a 360° view for?"
            )
            return ChatResponse(
                answer=answer,
                intent=operation,
                conversation_id=conversation_id,
                data={},
                sources=[],
            )

        period_days = min(
            max(int(args.get("period_days") or 90), 1),
            365,
        )

        psa_resolution, billing_resolution = await asyncio.gather(
            revio.resolve_customer(customer_name),
            revio.resolve_billing_customer(customer_name),
        )

        psa_matches = psa_resolution.get("matches") or []
        billing_matches = billing_resolution.get("matches") or []

        if (
            not psa_resolution.get("resolved")
            and not billing_resolution.get("resolved")
        ):
            combined_names: list[str] = []
            for match in psa_matches[:5]:
                name = revio._customer_name(match)
                if name not in combined_names:
                    combined_names.append(name)
            for match in billing_matches[:5]:
                name = revio._billing_customer_name(match)
                if name not in combined_names:
                    combined_names.append(name)

            data = {
                "customer_query": customer_name,
                "customer_matches": combined_names,
                "presentation_mode": "customer_confirmation",
            }

            if combined_names:
                answer = (
                    "I found more than one possible customer. "
                    "Which one did you mean?\n\n- "
                    + "\n- ".join(combined_names)
                )
            else:
                answer = (
                    f"I could not find a customer matching "
                    f"**{customer_name}**."
                )

            return ChatResponse(
                answer=answer,
                intent=operation,
                conversation_id=conversation_id,
                data=data,
                sources=[
                    SourceReference(
                        system="Rev.io",
                        label="Customer entity resolution",
                    )
                ],
            )

        billing_resolved_name = str(
            billing_resolution.get("customer_name") or ""
        ).strip()
        if billing_resolved_name.casefold() == "unnamed customer":
            billing_resolved_name = ""

        canonical_name = (
            billing_resolved_name
            or psa_resolution.get("customer_name")
            or customer_name
        )

        psa_customer_id = (
            int(psa_resolution["customer_id"])
            if psa_resolution.get("resolved")
            else None
        )
        billing_customer_id = (
            int(billing_resolution["customer_id"])
            if billing_resolution.get("resolved")
            else None
        )

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=period_days)

        async def safe_customer_call(
            name: str,
            coroutine: Any,
            default: Any,
        ) -> tuple[str, Any, str | None]:
            try:
                return name, await coroutine, None
            except Exception as exc:
                return name, default, str(exc)

        async def _all_billing_customers(
            *,
            page_size: int = 500,
            max_pages: int = 40,
        ) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for page in range(1, min(max(max_pages, 1), 100) + 1):
                page_rows = await revio.search_billing_customers(
                    page=page,
                    page_size=min(max(page_size, 1), 500),
                )
                rows.extend(page_rows)
                if len(page_rows) < min(max(page_size, 1), 500):
                    break
            return rows

        def _possible_customer_id(record: dict[str, Any]) -> int | None:
            for key in (
                "customer_id",
                "customerId",
                "CustomerId",
                "CustomerID",
                "id",
                "Id",
                "ID",
            ):
                value = record.get(key)
                if value in (None, ""):
                    continue
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
            return None

        def _possible_customer_name(record: dict[str, Any]) -> str:
            for key in (
                "customer_name",
                "customerName",
                "CustomerName",
                "name",
                "Name",
                "companyName",
                "CompanyName",
                "accountName",
                "AccountName",
            ):
                value = record.get(key)
                if value not in (None, ""):
                    return str(value)

            for address_key in (
                "billing_address",
                "billingAddress",
                "BillingAddress",
                "service_address",
                "serviceAddress",
                "ServiceAddress",
                "listing_address",
                "listingAddress",
                "ListingAddress",
            ):
                address = record.get(address_key)
                if not isinstance(address, dict):
                    continue

                for name_key in (
                    "company_name",
                    "companyName",
                    "CompanyName",
                    "name",
                    "Name",
                ):
                    value = address.get(name_key)
                    if value not in (None, ""):
                        return str(value)

            return ""

        def _possible_parent_id(record: dict[str, Any]) -> int | None:
            for key in (
                "parent_customer_id",
                "parentCustomerId",
                "ParentCustomerId",
                "ParentCustomerID",
                "parent_id",
                "parentId",
                "ParentId",
                "ParentID",
                "parent_account_id",
                "parentAccountId",
                "ParentAccountId",
                "ParentAccountID",
                "master_customer_id",
                "masterCustomerId",
                "MasterCustomerId",
                "MasterCustomerID",
            ):
                value = record.get(key)
                if value in (None, ""):
                    continue
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue

            for key in (
                "parent",
                "parentCustomer",
                "ParentCustomer",
                "parentAccount",
                "ParentAccount",
                "masterCustomer",
                "MasterCustomer",
            ):
                nested = record.get(key)
                if isinstance(nested, dict):
                    nested_id = _possible_customer_id(nested)
                    if nested_id is not None:
                        return nested_id

            return None

        async def _billing_account_family(
            customer_id: int,
        ) -> list[dict[str, Any]]:
            """
            Return the full Rev.io Billing family for a resolved customer.

            If the supplied customer is a child/location account, first walk
            upward to the root parent, then include every descendant below
            that parent. This prevents Customer 360 from missing ledger data
            simply because name resolution landed on a child account.
            """
            try:
                customers = await _all_billing_customers()
            except Exception:
                customers = []

            by_id: dict[int, dict[str, Any]] = {}
            for customer in customers:
                current_id = _possible_customer_id(customer)
                if current_id is not None:
                    by_id[current_id] = customer

            # Climb to the root parent when Rev.io returns a child/location.
            root_id = customer_id
            seen_upward: set[int] = set()

            while root_id not in seen_upward:
                seen_upward.add(root_id)
                row = by_id.get(root_id)
                if not row:
                    break

                parent_id = _possible_parent_id(row)
                if parent_id is None or parent_id == root_id:
                    break

                root_id = parent_id

            family: list[dict[str, Any]] = []
            root_row = by_id.get(root_id)
            family.append(
                root_row
                or {
                    "customer_id": root_id,
                    "customer_name": canonical_name,
                }
            )

            seen = {root_id}
            changed = True

            while changed:
                changed = False

                for current_id, customer in by_id.items():
                    if current_id in seen:
                        continue

                    parent_id = _possible_parent_id(customer)
                    if parent_id in seen:
                        family.append(customer)
                        seen.add(current_id)
                        changed = True

            # If the originally resolved customer was not returned by the
            # all-customers search, retain it as a direct ledger target too.
            if customer_id not in seen:
                family.append(
                    by_id.get(customer_id)
                    or {
                        "customer_id": customer_id,
                        "customer_name": canonical_name,
                    }
                )

            return family

        async def _rest_rows_for_customer(
            customer_id: int,
            *,
            created_date_start: str | None,
            created_date_end: str | None,
            page_size: int,
            max_pages: int,
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            normalized_page_size = min(max(page_size, 1), 500)
            normalized_max_pages = min(max(max_pages, 1), 100)

            async def fetch_all(fetcher: Any) -> list[dict[str, Any]]:
                rows: list[dict[str, Any]] = []
                for page in range(1, normalized_max_pages + 1):
                    page_rows = await fetcher(
                        customer_id=customer_id,
                        created_date_start=created_date_start,
                        created_date_end=created_date_end,
                        page=page,
                        page_size=normalized_page_size,
                    )
                    rows.extend(page_rows)
                    if len(page_rows) < normalized_page_size:
                        break
                return rows

            return await asyncio.gather(
                fetch_all(revio.search_billing_charges),
                fetch_all(revio.search_billing_credits),
            )

        async def customer_recent_ledger(
            customer_id: int,
            *,
            created_date_start: str | None = None,
            created_date_end: str | None = None,
            page_size: int = 500,
        ) -> dict[str, Any]:
            """
            Build the Customer 360 billing ledger using the proven Rev.io
            customer ledger helper.

            Each detected parent/child Billing account is queried separately
            for the selected date range. The connector returns each account's
            ledger newest-first, including SOAP Transactions_Query data when
            available. Results are combined and sorted newest-first.
            """
            family = await _billing_account_family(customer_id)

            account_ids: list[int] = []
            account_names: dict[int, str] = {}

            for customer in family:
                cid = _possible_customer_id(customer)
                if cid is None or cid in account_ids:
                    continue
                account_ids.append(cid)
                account_names[cid] = (
                    _possible_customer_name(customer)
                    or (
                        canonical_name
                        if cid == customer_id
                        else f"Billing customer {cid}"
                    )
                )

            if customer_id not in account_ids:
                account_ids.insert(0, customer_id)
                account_names[customer_id] = canonical_name

            async def fetch_account(
                cid: int,
            ) -> tuple[int, dict[str, Any] | None, str | None]:
                try:
                    ledger = await revio.get_billing_customer_ledger(
                        customer_id=cid,
                        created_date_start=created_date_start,
                        created_date_end=created_date_end,
                        page_size=page_size,
                    )
                    return cid, ledger, None
                except Exception as exc:
                    return cid, None, str(exc)

            fetched = await asyncio.gather(
                *(fetch_account(cid) for cid in account_ids)
            )

            combined_entries: list[dict[str, Any]] = []
            errors_by_account: dict[str, str] = {}
            sources: set[str] = set()
            charge_count = 0
            credit_count = 0
            payment_count = 0
            total_charges = 0.0
            total_credits = 0.0
            total_payments = 0.0

            signatures: set[tuple[str, str, str, str, str, str]] = set()

            for cid, ledger, error in fetched:
                if error:
                    errors_by_account[str(cid)] = error
                    continue
                if not isinstance(ledger, dict):
                    continue

                source = str(ledger.get("ledger_source") or "").strip()
                if source:
                    sources.add(source)

                charge_count += int(ledger.get("charge_count") or 0)
                credit_count += int(ledger.get("credit_count") or 0)
                payment_count += int(ledger.get("payment_count") or 0)
                total_charges += float(ledger.get("total_charges") or 0)
                total_credits += float(ledger.get("total_credits") or 0)
                total_payments += float(ledger.get("total_payments") or 0)

                for raw_entry in ledger.get("entries") or []:
                    if not isinstance(raw_entry, dict):
                        continue

                    entry = dict(raw_entry)
                    entry["billing_customer_id"] = cid
                    entry["billing_account_name"] = account_names.get(
                        cid,
                        f"Billing customer {cid}",
                    )

                    signature = (
                        str(cid),
                        str(entry.get("entry_type") or ""),
                        str(entry.get("transaction_id") or ""),
                        str(entry.get("created_date") or ""),
                        str(entry.get("amount") or ""),
                        str(entry.get("description") or ""),
                    )
                    if signature in signatures:
                        continue

                    signatures.add(signature)
                    combined_entries.append(entry)

            combined_entries.sort(
                key=lambda item: (
                    str(item.get("created_date") or ""),
                    str(item.get("entry_type") or ""),
                    str(item.get("transaction_id") or ""),
                ),
                reverse=True,
            )

            newest_created_date = (
                combined_entries[0].get("created_date")
                if combined_entries
                else None
            )
            oldest_created_date = (
                combined_entries[-1].get("created_date")
                if combined_entries
                else None
            )

            return {
                "entries": combined_entries,
                "charge_count": charge_count,
                "credit_count": credit_count,
                "payment_count": payment_count,
                "total_charges": round(total_charges, 2),
                "total_credits": round(total_credits, 2),
                "total_payments": round(total_payments, 2),
                "net_charges_less_credits": round(
                    total_charges - total_credits,
                    2,
                ),
                "billing_account_count": len(account_ids),
                "billing_account_ids": account_ids,
                "billing_accounts": [
                    {
                        "customer_id": cid,
                        "name": account_names.get(
                            cid,
                            f"Billing customer {cid}",
                        ),
                    }
                    for cid in account_ids
                ],
                "ledger_source": (
                    ", ".join(sorted(sources))
                    if sources
                    else "customer_ledger_unavailable"
                ),
                "payment_data_included": payment_count > 0,
                "newest_created_date": newest_created_date,
                "oldest_created_date": oldest_created_date,
                "returned_order": "newest_first",
                "account_errors": errors_by_account,
            }

        calls = [
            safe_customer_call(
                "tickets",
                revio.search_tickets(
                    customer_name=canonical_name,
                    page_size=500,
                    fetch_all=True,
                ),
                [],
            ),
            safe_customer_call(
                "projects",
                revio.search_projects(
                    customer_id=psa_customer_id,
                    page=1,
                    per_page=500,
                )
                if psa_customer_id is not None
                else asyncio.sleep(0, result=[]),
                [],
            ),
            safe_customer_call(
                "opportunities",
                revio.search_opportunities(
                    customer_id=psa_customer_id,
                    page=1,
                    per_page=500,
                )
                if psa_customer_id is not None
                else asyncio.sleep(0, result=[]),
                [],
            ),
            safe_customer_call(
                "contacts",
                revio.search_billing_contacts(
                    customer_id=billing_customer_id,
                    page=1,
                    page_size=500,
                )
                if billing_customer_id is not None
                else asyncio.sleep(0, result=[]),
                [],
            ),
            safe_customer_call(
                "billing",
                customer_recent_ledger(
                    billing_customer_id,
                    created_date_start=start_date.isoformat(),
                    created_date_end=end_date.isoformat(),
                    page_size=500,
                )
                if billing_customer_id is not None
                else asyncio.sleep(
                    0,
                    result={
                        "entries": [],
                        "charge_count": 0,
                        "credit_count": 0,
                        "total_charges": 0,
                        "total_credits": 0,
                        "net_charges_less_credits": 0,
                        "ledger_source": "customer_not_resolved",
                    },
                ),
                {
                    "entries": [],
                    "charge_count": 0,
                    "credit_count": 0,
                    "total_charges": 0,
                    "total_credits": 0,
                    "net_charges_less_credits": 0,
                    "ledger_source": "rest_customer_ledger_failed",
                },
            ),
            safe_customer_call(
                "services",
                revio.search_billing_service_products(
                    customer_id=billing_customer_id,
                    page=1,
                    page_size=500,
                )
                if billing_customer_id is not None
                else asyncio.sleep(0, result=[]),
                [],
            ),
            safe_customer_call(
                "renewals",
                asyncio.to_thread(get_renewal_snapshot),
                {
                    "ccwr_renewals": [],
                    "renewal_summary": {},
                },
            ),
        ]

        gathered = await asyncio.gather(*calls)
        values = {name: value for name, value, _ in gathered}
        errors = {
            name: error
            for name, _, error in gathered
            if error
        }

        # If the selected-period ledger is empty, retry once without a date
        # range. This distinguishes "no activity in the last 90 days" from
        # "this customer has no billing ledger data at all".
        period_billing = (
            values.get("billing")
            if isinstance(values.get("billing"), dict)
            else {}
        )
        period_entries = (
            period_billing.get("entries")
            or period_billing.get("ledger_entries")
            or []
        )
        billing_fallback_used = False

        if (
            billing_customer_id is not None
            and not period_entries
            and "billing" not in errors
        ):
            try:
                all_time_billing = await customer_recent_ledger(
                    billing_customer_id,
                    page_size=500,
                )
                all_time_entries = (
                    all_time_billing.get("entries")
                    or all_time_billing.get("ledger_entries")
                    or []
                )
                if all_time_entries:
                    values["billing"] = all_time_billing
                    billing_fallback_used = True
            except Exception as exc:
                errors["billing_fallback"] = str(exc)

        tickets = _records_from_response(
            values["tickets"],
            preferred_keys=("tickets",),
        )
        projects = _records_from_response(
            values["projects"],
            preferred_keys=("projects",),
        )
        opportunities = _records_from_response(
            values["opportunities"],
            preferred_keys=("opportunities",),
        )
        contacts = _records_from_response(
            values["contacts"],
            preferred_keys=("contacts",),
        )
        billing = (
            values["billing"]
            if isinstance(values["billing"], dict)
            else {}
        )
        service_products = _records_from_response(
            values.get("services"),
            preferred_keys=(
                "serviceProducts",
                "service_products",
                "services",
                "products",
            ),
        )
        active_services = [
            product
            for product in service_products
            if _is_active_service_product(product)
        ]

        renewal_snapshot = (
            values["renewals"]
            if isinstance(values["renewals"], dict)
            else {}
        )
        all_renewals = _records_from_response(
            renewal_snapshot,
            preferred_keys=("ccwr_renewals",),
        )

        canonical_fold = canonical_name.casefold()
        customer_renewals = [
            renewal
            for renewal in all_renewals
            if canonical_fold
            in str(
                renewal.get("end_customer_name")
                or renewal.get("customerName")
                or ""
            ).casefold()
        ]

        active_tickets = active_only(tickets)
        active_projects = [
            project
            for project in projects
            if str(
                project.get("projectStatusName")
                or project.get("status")
                or ""
            ).strip().casefold()
            in {"active", "open", "in progress", "in-progress"}
        ]

        billing_entries = billing.get("ledger_entries") or billing.get("entries") or []
        total_charges = float(billing.get("total_charges") or 0)
        total_credits = float(billing.get("total_credits") or 0)
        net_billing = float(
            billing.get("net_billing_activity")
            or billing.get("net_charges_less_credits")
            or (total_charges - total_credits)
        )

        customer_report = _standard_report(
            report_type="customer_360",
            title=f"{canonical_name} — Customer 360",
            source="Ribbit Intelligence",
            period=f"Last {period_days} days",
            summary=(
                f"{canonical_name} has {len(active_tickets)} active tickets, "
                f"{len(active_projects)} active projects, "
                f"{len(opportunities)} opportunities, "
                f"{len(customer_renewals)} Cisco renewals, "
                f"{len(active_services)} active billing services, and "
                f"${net_billing:,.2f} in net billing activity."
            ),
            kpis=[
                {
                    "label": "Active Tickets",
                    "value": len(active_tickets),
                    "format": "number",
                },
                {
                    "label": "Active Projects",
                    "value": len(active_projects),
                    "format": "number",
                },
                {
                    "label": "Opportunities",
                    "value": len(opportunities),
                    "format": "number",
                },
                {
                    "label": "Renewals",
                    "value": len(customer_renewals),
                    "format": "number",
                },
                {
                    "label": "Net Billing",
                    "value": net_billing,
                    "format": "currency",
                },
                {
                    "label": "Contacts",
                    "value": len(contacts),
                    "format": "number",
                },
                {
                    "label": "Active Services",
                    "value": len(active_services),
                    "format": "number",
                },
            ],
            attention_items=[
                {
                    "severity": "Review",
                    "title": f"{area} data unavailable",
                    "detail": error,
                }
                for area, error in errors.items()
            ],
            detail_sections=[
                {
                    "title": "Contacts",
                    "columns": [
                        {"key": "name", "label": "Name"},
                        {"key": "email", "label": "Email"},
                        {"key": "phone", "label": "Phone"},
                        {"key": "title", "label": "Title"},
                    ],
                    "rows": [
                        {
                            "name": (
                                contact.get("Name")
                                or contact.get("name")
                                or "—"
                            ),
                            "email": (
                                contact.get("Email")
                                or contact.get("email")
                                or "—"
                            ),
                            "phone": (
                                contact.get("Phone")
                                or contact.get("phone")
                                or contact.get("PhoneNumber")
                                or "—"
                            ),
                            "title": (
                                contact.get("Title")
                                or contact.get("title")
                                or "—"
                            ),
                        }
                        for contact in contacts[:200]
                    ],
                },
                {
                    "title": "Ticket Details",
                    "columns": [
                        {"key": "ticket_id", "label": "Ticket ID"},
                        {"key": "subject", "label": "Subject"},
                        {"key": "status", "label": "Status"},
                        {"key": "priority", "label": "Priority"},
                        {"key": "engineer", "label": "Engineer"},
                        {"key": "age_days", "label": "Age", "type": "days"},
                    ],
                    "rows": [
                        {
                            "ticket_id": (
                                ticket.get("ticket_id")
                                or ticket.get("ticketId")
                                or ticket.get("id")
                                or "—"
                            ),
                            "subject": ticket.get("subject") or "—",
                            "status": ticket.get("status") or "—",
                            "priority": ticket.get("priority") or "—",
                            "engineer": (
                                ticket.get("assigned_engineer")
                                or ticket.get("assignedEngineer")
                                or "Unassigned"
                            ),
                            "age_days": ticket.get("age_days") or 0,
                        }
                        for ticket in tickets[:200]
                    ],
                },
                {
                    "title": "Project Details",
                    "columns": [
                        {"key": "project", "label": "Project"},
                        {"key": "status", "label": "Status"},
                        {"key": "owner", "label": "Owner"},
                        {"key": "start", "label": "Start", "type": "date"},
                        {"key": "end", "label": "End", "type": "date"},
                    ],
                    "rows": [
                        {
                            "project": (
                                project.get("projectName")
                                or project.get("name")
                                or "—"
                            ),
                            "status": (
                                project.get("projectStatusName")
                                or project.get("status")
                                or "—"
                            ),
                            "owner": (
                                project.get("projectManagerName")
                                or project.get("ownerName")
                                or "—"
                            ),
                            "start": project.get("startDate"),
                            "end": project.get("endDate"),
                        }
                        for project in projects[:200]
                    ],
                },
                {
                    "title": "Opportunity Details",
                    "columns": [
                        {"key": "opportunity", "label": "Opportunity"},
                        {"key": "stage", "label": "Stage"},
                        {"key": "owner", "label": "Owner"},
                        {"key": "amount", "label": "Amount", "type": "currency"},
                        {"key": "close_date", "label": "Close Date", "type": "date"},
                    ],
                    "rows": [
                        {
                            "opportunity": (
                                opportunity.get("name")
                                or opportunity.get("title")
                                or opportunity.get("subject")
                                or "—"
                            ),
                            "stage": (
                                opportunity.get("stageName")
                                or opportunity.get("stage")
                                or opportunity.get("status")
                                or "—"
                            ),
                            "owner": (
                                opportunity.get("ownerName")
                                or opportunity.get("owner")
                                or "—"
                            ),
                            "amount": _number_from_record(
                                opportunity,
                                (
                                    "amount",
                                    "Amount",
                                    "value",
                                    "estimatedValue",
                                ),
                            ),
                            "close_date": (
                                opportunity.get("expectedCloseDate")
                                or opportunity.get("closeDate")
                            ),
                        }
                        for opportunity in opportunities[:200]
                    ],
                },
                {
                    "title": "Billing Details",
                    "columns": [
                        {"key": "metric", "label": "Metric"},
                        {"key": "value", "label": "Value"},
                    ],
                    "rows": [
                        {
                            "metric": "Billing Customer ID",
                            "value": billing_customer_id or "Not resolved",
                        },
                        {
                            "metric": "Period",
                            "value": (
                                "All available history (90-day period was empty)"
                                if billing_fallback_used
                                else f"Last {period_days} days"
                            ),
                        },
                        {
                            "metric": "Ledger API status",
                            "value": (
                                "Error"
                                if "billing" in errors
                                else "Connected"
                            ),
                        },
                        {
                            "metric": "Ledger source",
                            "value": (
                                billing.get("ledger_source")
                                or "Unknown"
                            ),
                        },
                        {
                            "metric": "Newest ledger activity",
                            "value": (
                                billing.get("newest_created_date")
                                or "No ledger activity"
                            ),
                        },
                        {
                            "metric": "Oldest ledger activity in result",
                            "value": (
                                billing.get("oldest_created_date")
                                or "No ledger activity"
                            ),
                        },
                        {
                            "metric": "Ledger order",
                            "value": (
                                billing.get("returned_order")
                                or "newest_first"
                            ),
                        },
                        {
                            "metric": "Billing accounts included",
                            "value": int(
                                billing.get("billing_account_count") or 1
                            ),
                        },
                        {
                            "metric": "Payments",
                            "value": float(
                                billing.get("total_payments") or 0
                            ),
                            "value_type": "currency",
                        },
                        {
                            "metric": "Adjustments",
                            "value": float(
                                billing.get("total_adjustments") or 0
                            ),
                            "value_type": "currency",
                        },
                        {
                            "metric": "Ledger running balance",
                            "value": float(
                                billing.get("ledger_running_balance") or 0
                            ),
                            "value_type": "currency",
                        },
                        {
                            "metric": "Charge records",
                            "value": int(
                                billing.get("charge_count") or 0
                            ),
                        },
                        {
                            "metric": "Credit records",
                            "value": int(
                                billing.get("credit_count") or 0
                            ),
                        },
                        {
                            "metric": "Total charges",
                            "value": total_charges,
                            "value_type": "currency",
                        },
                        {
                            "metric": "Total credits",
                            "value": total_credits,
                            "value_type": "currency",
                        },
                        {
                            "metric": "Net billing activity",
                            "value": net_billing,
                            "value_type": "currency",
                        },
                        {
                            "metric": "Ledger entries",
                            "value": len(billing_entries),
                        },
                        {
                            "metric": "Raw service products returned",
                            "value": len(service_products),
                        },
                        {
                            "metric": "Active services",
                            "value": len(active_services),
                        },
                        {
                            "metric": "Service API status",
                            "value": (
                                "Error"
                                if "services" in errors
                                else "Connected"
                            ),
                        },
                        {
                            "metric": "Billing API error",
                            "value": errors.get("billing") or "None",
                        },
                        {
                            "metric": "Service API error",
                            "value": errors.get("services") or "None",
                        },
                    ],
                },
                {
                    "title": "Active Services",
                    "columns": [
                        {"key": "service", "label": "Service / Product"},
                        {"key": "description", "label": "Description"},
                        {"key": "status", "label": "Status"},
                        {"key": "quantity", "label": "Qty"},
                        {"key": "amount", "label": "Amount", "type": "currency"},
                    ],
                    "rows": [
                        {
                            "service": (
                                product.get("ProductName")
                                or product.get("productName")
                                or product.get("ServiceName")
                                or product.get("serviceName")
                                or product.get("name")
                                or product.get("Description")
                                or product.get("description")
                                or "—"
                            ),
                            "description": (
                                product.get("Description")
                                or product.get("description")
                                or product.get("ProductDescription")
                                or product.get("productDescription")
                                or "—"
                            ),
                            "status": (
                                product.get("Status")
                                or product.get("status")
                                or product.get("serviceStatusName")
                                or product.get("productStatusName")
                                or "Active"
                            ),
                            "quantity": (
                                product.get("Quantity")
                                or product.get("quantity")
                                or product.get("Qty")
                                or 1
                            ),
                            "amount": _number_from_record(
                                product,
                                (
                                    "Amount",
                                    "amount",
                                    "Price",
                                    "price",
                                    "UnitPrice",
                                    "unitPrice",
                                    "RecurringAmount",
                                    "recurringAmount",
                                ),
                            ),
                        }
                        for product in active_services[:200]
                    ],
                },
                {
                    "title": "Recent Billing Activity",
                    "columns": [
                        {"key": "date", "label": "Date", "type": "date"},
                        {"key": "account", "label": "Account"},
                        {"key": "type", "label": "Type"},
                        {"key": "description", "label": "Description"},
                        {"key": "amount", "label": "Amount", "type": "currency"},
                        {"key": "balance", "label": "Running Balance", "type": "currency"},
                        {"key": "bill_id", "label": "Bill ID"},
                    ],
                    "rows": [
                        {
                            "date": (
                                entry.get("created_date")
                                or entry.get("CreatedDate")
                                or entry.get("date")
                            ),
                            "account": (
                                entry.get("billing_account_name")
                                or entry.get("billing_customer_id")
                                or "—"
                            ),
                            "type": (
                                entry.get("entry_type")
                                or entry.get("EntryType")
                                or entry.get("type")
                                or "Transaction"
                            ),
                            "description": (
                                entry.get("description")
                                or entry.get("Description")
                                or "—"
                            ),
                            "amount": (
                                float(entry.get("signed_amount"))
                                if entry.get("signed_amount") not in (None, "")
                                else _number_from_record(
                                    entry,
                                    ("amount", "Amount", "total", "Total"),
                                )
                            ),
                            "balance": (
                                entry.get("running_charge_credit_balance")
                            ),
                            "bill_id": (
                                entry.get("bill_id")
                                or entry.get("BillId")
                                or entry.get("billId")
                                or "—"
                            ),
                        }
                        for entry in sorted(
                            billing_entries,
                            key=lambda item: str(
                                item.get("created_date")
                                or item.get("CreatedDate")
                                or item.get("date")
                                or ""
                            ),
                            reverse=True,
                        )[:50]
                    ],
                },
                {
                    "title": "Renewal Details",
                    "columns": [
                        {"key": "subscription_id", "label": "Subscription ID"},
                        {"key": "market", "label": "Market"},
                        {"key": "status", "label": "Status"},
                        {"key": "renewal_date", "label": "Renewal Date", "type": "date"},
                        {"key": "days", "label": "Days Remaining"},
                    ],
                    "rows": [
                        {
                            "subscription_id": (
                                renewal.get("subscription_id")
                                or renewal.get("subscriptionId")
                                or "—"
                            ),
                            "market": renewal.get("market") or "—",
                            "status": (
                                renewal.get("subscription_status")
                                or renewal.get("status")
                                or "—"
                            ),
                            "renewal_date": (
                                renewal.get("dashboard_renewal_date")
                                or renewal.get("renewal_date")
                            ),
                            "days": renewal.get("days_until_renewal"),
                        }
                        for renewal in customer_renewals[:200]
                    ],
                },
            ],
        )

        data = {
            "customer_360": {
                "customer_name": canonical_name,
                "psa_customer_id": psa_customer_id,
                "billing_customer_id": billing_customer_id,
            },
            "contacts": contacts,
            "tickets": tickets,
            "projects": projects,
            "opportunities": opportunities,
            "billing": billing,
            "service_products": service_products,
            "active_services": active_services,
            "ccwr_renewals": customer_renewals,
            "standard_reports": [customer_report],
            "primary_report": customer_report,
            "presentation_mode": "customer_360",
        }
        label = "Combined Customer 360 data interpreted by Ribbit"

        # Customer 360 is a complete response. Return it here so a later
        # operation dispatch chain can never overwrite it.
        answer = (
            f"Customer 360 for {canonical_name}. "
            "Open the sections below for contacts, tickets, projects, "
            "opportunities, billing, and renewals."
        )
        _save_conversation(
            conversation_id,
            question=request.message,
            answer=answer,
            intent=operation,
            data=data,
        )
        return ChatResponse(
            answer=answer,
            intent=operation,
            conversation_id=conversation_id,
            data=data,
            sources=[
                SourceReference(
                    system="Ribbit Intelligence",
                    label=label,
                )
            ],
        )

    elif operation == "company_health_snapshot":
        period_days = min(
            max(int(args.get("period_days") or 30), 1),
            365,
        )
        ccwr_lookback_days = min(
            max(
                int(args.get("ccwr_lookback_days") or 180),
                30,
            ),
            settings.cisco_max_lookback_days,
        )
        market_arg = str(args.get("market") or "All")
        billing_end_date = datetime.now(
            timezone.utc
        ).date()
        billing_start_date = (
            billing_end_date - timedelta(days=period_days)
        )

        markets = (
            ["US", "Canada"]
            if market_arg.casefold().strip()
            in {"all", "both", "us and canada"}
            else [market_arg]
        )

        async def safe_call(
            name: str,
            coroutine: Any,
            default: Any,
        ) -> tuple[str, Any, str | None]:
            try:
                return name, await coroutine, None
            except Exception as exc:
                return name, default, str(exc)

        results = await asyncio.gather(
            safe_call(
                "Rev.io PSA tickets",
                revio.search_tickets(
                    page_size=500,
                    fetch_all=True,
                ),
                [],
            ),
            safe_call(
                "Rev.io projects",
                revio.search_projects(
                    page=1,
                    per_page=500,
                ),
                [],
            ),
            safe_call(
                "Rev.io opportunities",
                revio.search_opportunities(
                    page=1,
                    per_page=500,
                ),
                [],
            ),
            safe_call(
                "Rev.io Billing customers",
                revio.search_billing_customers(
                    page=1,
                    page_size=500,
                ),
                [],
            ),
            safe_call(
                "Rev.io Billing ledger",
                revio.get_company_billing_ledger(
                    created_date_start=(
                        billing_start_date.isoformat()
                    ),
                    created_date_end=(
                        billing_end_date.isoformat()
                    ),
                    page_size=500,
                    max_pages=40,
                ),
                {
                    "entries": [],
                    "charge_count": 0,
                    "credit_count": 0,
                    "total_charges": 0,
                    "total_credits": 0,
                    "net_charges_less_credits": 0,
                    "customers_with_activity": 0,
                    "payment_data_included": False,
                },
            ),
            safe_call(
                "Ribbit renewal database",
                asyncio.to_thread(get_renewal_snapshot),
                {
                    "ccwr_renewals": [],
                    "renewal_summary": {},
                },
            ),
        )

        values = {
            name: value
            for name, value, _ in results
        }
        errors = {
            name: error
            for name, _, error in results
            if error
        }
        ccwr_result = values["Ribbit renewal database"]

        normalized_tickets = _records_from_response(
            values["Rev.io PSA tickets"],
            preferred_keys=("tickets",),
        )
        normalized_projects = _records_from_response(
            values["Rev.io projects"],
            preferred_keys=("projects",),
        )
        normalized_opportunities = _records_from_response(
            values["Rev.io opportunities"],
            preferred_keys=("opportunities",),
        )
        normalized_billing_customers = _records_from_response(
            values["Rev.io Billing customers"],
            preferred_keys=("customers",),
        )
        billing_ledger = (
            values["Rev.io Billing ledger"]
            if isinstance(
                values["Rev.io Billing ledger"],
                dict,
            )
            else {}
        )
        normalized_renewals = _records_from_response(
            ccwr_result,
            preferred_keys=("ccwr_renewals",),
        )

        data = _build_company_snapshot(
            period_days=period_days,
            tickets=normalized_tickets,
            projects=normalized_projects,
            opportunities=normalized_opportunities,
            billing_customers=normalized_billing_customers,
            billing_ledger=billing_ledger,
            renewals=normalized_renewals,
            renewal_summary=(
                ccwr_result.get("renewal_summary") or {}
            ),
            errors=errors,
        )
        data["filters"] = {
            "period_days": period_days,
            "ccwr_lookback_days": ccwr_lookback_days,
            "market": market_arg,
        }
        label = (
            "Live combined Bullfrog company health data "
            "interpreted by OpenAI"
        )

    elif operation == "ccwr_search_renewals":
        market_arg = str(args.get("market") or "All").strip()

        requested_market_tokens = {
            token.strip().casefold()
            for token in market_arg.replace("&", " and ").replace(",", " ").split()
            if token.strip()
        }

        all_market_requests = {
            "all",
            "both",
            "us and canada",
            "usa and canada",
            "united states and canada",
            "canada and us",
            "canada and usa",
            "north america",
        }

        include_all_markets = (
            not market_arg
            or market_arg.casefold() in all_market_requests
            or "all" in requested_market_tokens
            or "both" in requested_market_tokens
            or (
                any(
                    token in requested_market_tokens
                    for token in {
                        "us",
                        "usa",
                        "united",
                        "states",
                    }
                )
                and any(
                    token in requested_market_tokens
                    for token in {
                        "canada",
                        "ca",
                        "can",
                    }
                )
            )
        )

        def normalize_market(value: Any) -> str:
            market_value = str(value or "").strip().casefold()

            if market_value in {
                "us",
                "usa",
                "u.s.",
                "u.s.a.",
                "united states",
                "united states of america",
                "unitedstates",
                "america",
            }:
                return "US"

            if market_value in {
                "canada",
                "ca",
                "can",
                "canadian",
            }:
                return "Canada"

            return str(value or "").strip()

        requested_markets: set[str] = set()

        if include_all_markets:
            requested_markets = {"US", "Canada"}
        else:
            normalized_requested = normalize_market(market_arg)
            if normalized_requested:
                requested_markets.add(normalized_requested)

        renewal_scope = str(
            args.get("renewal_scope") or "all"
        ).strip()

        result = await asyncio.to_thread(get_renewal_snapshot)
        database_records = result.get("ccwr_renewals") or []

        normalized_database_records = []

        for raw_record in database_records:
            record = dict(raw_record)

            normalized_market = normalize_market(
                record.get("market")
                or record.get("country")
                or record.get("region")
            )
            record["market"] = normalized_market
            record["Market"] = normalized_market

            subscription_id = (
                record.get("subscription_id")
                or record.get("subscriptionId")
                or record.get("Subscription ID")
                or record.get("id")
            )
            record["subscription_id"] = subscription_id
            record["subscriptionId"] = subscription_id
            record["Subscription ID"] = subscription_id

            customer_name = (
                record.get("end_customer_name")
                or record.get("endCustomerName")
                or record.get("customer_name")
                or record.get("customerName")
                or record.get("End Customer Name")
                or record.get("customer")
            )
            record["end_customer_name"] = customer_name
            record["endCustomerName"] = customer_name
            record["customer_name"] = customer_name
            record["customerName"] = customer_name
            record["End Customer Name"] = customer_name

            renewal_date_value = (
                record.get("renewal_date")
                or record.get("renewalDate")
                or record.get("Renewal Date")
                or record.get("end_date")
                or record.get("endDate")
            )

            parsed_renewal_date = None
            if isinstance(renewal_date_value, datetime):
                parsed_renewal_date = renewal_date_value
            elif renewal_date_value:
                renewal_text = str(renewal_date_value).strip()
                parse_candidates = [
                    renewal_text,
                    renewal_text.replace("Z", "+00:00"),
                ]
                for candidate in parse_candidates:
                    try:
                        parsed_renewal_date = datetime.fromisoformat(
                            candidate
                        )
                        break
                    except ValueError:
                        continue

                if parsed_renewal_date is None:
                    for date_format in (
                        "%Y-%m-%d",
                        "%m/%d/%Y",
                        "%m/%d/%y",
                    ):
                        try:
                            parsed_renewal_date = datetime.strptime(
                                renewal_text[:10],
                                date_format,
                            )
                            break
                        except ValueError:
                            continue

            if (
                parsed_renewal_date is not None
                and parsed_renewal_date.tzinfo is None
            ):
                parsed_renewal_date = parsed_renewal_date.replace(
                    tzinfo=timezone.utc
                )

            normalized_renewal_date = (
                parsed_renewal_date.date().isoformat()
                if parsed_renewal_date is not None
                else renewal_date_value
            )

            record["renewal_date"] = normalized_renewal_date
            record["renewalDate"] = normalized_renewal_date
            record["Renewal Date"] = normalized_renewal_date

            subscription_status = str(
                record.get("subscription_status")
                or record.get("status")
                or record.get("subscriptionStatus")
                or record.get("Subscription Status")
                or ""
            ).strip().upper()
            record["status"] = subscription_status
            record["subscription_status"] = subscription_status
            record["subscriptionStatus"] = subscription_status
            record["Subscription Status"] = subscription_status

            days_until_renewal = (
                record.get("days_until_renewal")
                or record.get("daysUntilRenewal")
            )

            try:
                normalized_days_until_renewal = int(
                    days_until_renewal
                )
            except (TypeError, ValueError):
                if parsed_renewal_date is not None:
                    normalized_days_until_renewal = (
                        parsed_renewal_date.date()
                        - datetime.now(timezone.utc).date()
                    ).days
                else:
                    normalized_days_until_renewal = None

            record["days_until_renewal"] = (
                normalized_days_until_renewal
            )
            record["daysUntilRenewal"] = (
                normalized_days_until_renewal
            )

            if normalized_days_until_renewal is None:
                renewal_bucket = (
                    record.get("renewal_bucket")
                    or record.get("renewalBucket")
                    or record.get("Renewal Bucket")
                    or "Unknown"
                )
            elif normalized_days_until_renewal < 0:
                renewal_bucket = "Past Due"
            elif normalized_days_until_renewal <= 30:
                renewal_bucket = "0-30"
            elif normalized_days_until_renewal <= 60:
                renewal_bucket = "31-60"
            elif normalized_days_until_renewal <= 90:
                renewal_bucket = "61-90"
            else:
                renewal_bucket = "91+"

            record["renewal_bucket"] = renewal_bucket
            record["renewalBucket"] = renewal_bucket
            record["Renewal Bucket"] = renewal_bucket

            record["is_past_due"] = (
                normalized_days_until_renewal is not None
                and normalized_days_until_renewal < 0
                and subscription_status == "ACTIVE"
            )

            record["is_closed"] = subscription_status in {
                "EXPIRED",
                "CANCELLED",
                "CANCELED",
                "CLOSED",
                "TERMINATED",
            }

            normalized_database_records.append(record)

        if include_all_markets:
            market_records = [
                record
                for record in normalized_database_records
                if record.get("market") in {"US", "Canada"}
            ]
        else:
            market_records = [
                record
                for record in normalized_database_records
                if record.get("market") in requested_markets
            ]

        records = ccwr.filter_renewals(
            market_records,
            customer_name=args.get("customer_name"),
            renewal_scope=renewal_scope,
            status=args.get("status"),
            active_only=bool(args.get("active_only")),
        )

        from .connectors.ccwr import summarize_renewals

        summary = summarize_renewals(records)

        calculated_due_30 = sum(
            1
            for record in records
            if isinstance(record.get("days_until_renewal"), int)
            and 0 <= record["days_until_renewal"] <= 30
        )
        calculated_due_31_60 = sum(
            1
            for record in records
            if isinstance(record.get("days_until_renewal"), int)
            and 31 <= record["days_until_renewal"] <= 60
        )
        calculated_due_61_90 = sum(
            1
            for record in records
            if isinstance(record.get("days_until_renewal"), int)
            and 61 <= record["days_until_renewal"] <= 90
        )
        calculated_overdue = sum(
            1
            for record in records
            if record.get("is_past_due")
        )
        calculated_closed = sum(
            1
            for record in records
            if record.get("is_closed")
        )
        calculated_active = sum(
            1
            for record in records
            if record.get("subscription_status") == "ACTIVE"
        )

        summary.update(
            {
                "total": len(records),
                "active": calculated_active,
                "overdue": calculated_overdue,
                "due_0_30": calculated_due_30,
                "due_within_30": calculated_due_30,
                "due_31_60": calculated_due_31_60,
                "due_61_90": calculated_due_61_90,
                "due_next_90": (
                    calculated_due_30
                    + calculated_due_31_60
                    + calculated_due_61_90
                ),
                "closed": calculated_closed,
            }
        )

        market_counts = {
            "US": 0,
            "Canada": 0,
        }

        for record in records:
            market = normalize_market(record.get("market"))
            if market in market_counts:
                market_counts[market] += 1

        database_market_counts = {
            "US": 0,
            "Canada": 0,
        }

        for record in normalized_database_records:
            market = record.get("market")
            if market in database_market_counts:
                database_market_counts[market] += 1

        summary.update(
            {
                "source": "Ribbit PostgreSQL",
                "market_counts": market_counts,
                "database_market_counts": database_market_counts,
                "us_subscriptions": market_counts["US"],
                "canada_subscriptions": market_counts["Canada"],
                "database_us_subscriptions": (
                    database_market_counts["US"]
                ),
                "database_canada_subscriptions": (
                    database_market_counts["Canada"]
                ),
                "markets_returned": [
                    market
                    for market in ("US", "Canada")
                    if market_counts[market] > 0
                ],
                "markets_available_in_database": [
                    market
                    for market in ("US", "Canada")
                    if database_market_counts[market] > 0
                ],
                "last_refreshed": (
                    result.get("renewal_summary") or {}
                ).get("last_refreshed"),
            }
        )

        data = {
            "ccwr_renewals": records,
            "renewal_summary": summary,
            "markets": (
                ["US", "Canada"]
                if include_all_markets
                else sorted(requested_markets)
            ),
            "customer_name": args.get("customer_name"),
            "renewal_scope": renewal_scope,
            "filters": {
                "market": market_arg,
                "markets_applied": (
                    ["US", "Canada"]
                    if include_all_markets
                    else sorted(requested_markets)
                ),
                "customer_name": args.get("customer_name"),
                "renewal_scope": renewal_scope,
                "status": args.get("status"),
                "active_only": bool(args.get("active_only")),
            },
            "count": len(records),
            "source": "Ribbit PostgreSQL",
        }

        label = (
            "Combined US and Canada renewal data from Ribbit PostgreSQL "
            "interpreted by OpenAI"
        )

    elif operation == "revio_search_tickets":
        tickets = await revio.search_tickets(
            status=args.get("status"),
            customer_name=args.get("customer_name"),
            assigned_engineer=args.get("assigned_engineer"),
            minimum_age_days=args.get("minimum_age_days"),
            page_size=min(max(int(args.get("page_size") or 500), 1), 500),
        )
        if args.get("active_only"):
            tickets = active_only(tickets)
        data = {"tickets": tickets, "count": len(tickets), "filters": args}


    elif operation == "revio_billing_search_customers":
        customers = await revio.search_billing_customers(
            query=args.get("query"),
            customer_id=args.get("customer_id"),
            account_number=args.get("account_number"),
            page=int(args.get("page") or 1),
            page_size=min(max(int(args.get("page_size") or 100), 1), 500),
        )
        data = {
            "customers": customers,
            "count": len(customers),
            "source": "revio_billing",
        }
        label = "Live Rev.io Billing customer search interpreted by OpenAI"

    elif operation == "revio_billing_get_customer":
        customer_id = int(args["customer_id"])
        customer = await revio.get_billing_customer(customer_id)
        data = {
            "customer": customer,
            "source": "revio_billing",
        }
        label = "Live Rev.io Billing customer record interpreted by OpenAI"

    elif operation == "revio_billing_search_contacts":
        customer_id_value = args.get("customer_id")
        customer_name = str(
            args.get("customer_name") or ""
        ).strip()
        contact_query = str(args.get("query") or "").strip()

        message_lower = request.message.casefold()
        customer_contact_phrases = (
            "contacts for ",
            "contact for ",
            "contacts from ",
            "contact from ",
            "contacts at ",
            "contact at ",
            "contacts of ",
            "contact of ",
        )

        if (
            customer_id_value is None
            and not customer_name
            and contact_query
            and any(
                phrase in message_lower
                for phrase in customer_contact_phrases
            )
        ):
            customer_name = contact_query
            contact_query = ""

        resolved_customer = None
        resolved_customer_name = None

        if customer_id_value is not None:
            customer_id = int(customer_id_value)
        elif customer_name:
            resolution = await revio.resolve_billing_customer(customer_name)
            matches = resolution.get("matches") or []

            if resolution.get("resolved"):
                resolved_customer_name = str(
                    resolution.get("customer_name") or ""
                ).strip()
                customer_id = int(resolution["customer_id"])
                resolved_customer = resolution.get("customer")
            elif len(matches) == 1:
                resolved_customer = matches[0]
                customer_id = int(
                    revio._billing_customer_id(resolved_customer)
                )
                resolved_customer_name = revio._billing_customer_name(
                    resolved_customer
                )
            else:
                data = {
                    "customer_confirmation_required": True,
                    "customer_query": customer_name,
                    "customers": matches[:10],
                    "count": len(matches[:10]),
                    "reason": resolution.get("reason"),
                    "source": "revio_billing",
                    "presentation_mode": "customer_confirmation",
                }

                if matches:
                    names = [
                        revio._billing_customer_name(match)
                        for match in matches[:5]
                    ]
                    answer = (
                        "I found more than one possible customer. "
                        "Which one did you mean?\n\n- "
                        + "\n- ".join(names)
                    )
                else:
                    answer = (
                        f"I could not find a customer matching "
                        f"**{customer_name}**. Please provide more of "
                        "the customer name."
                    )

                _save_conversation(
                    conversation_id,
                    question=request.message,
                    answer=answer,
                    intent="revio_contact_customer_confirmation",
                    data=data,
                )
                return ChatResponse(
                    answer=answer,
                    intent="revio_contact_customer_confirmation",
                    conversation_id=conversation_id,
                    data=data,
                    sources=[
                        SourceReference(
                            system="Rev.io Billing",
                            label="Customer-name search for contacts",
                        )
                    ],
                )
        else:
            customer_id = None

        contacts = await revio.search_billing_contacts(
            customer_id=customer_id,
            query=contact_query or None,
            page=int(args.get("page") or 1),
            page_size=min(max(int(args.get("page_size") or 100), 1), 500),
        )
        data = {
            "contacts": contacts,
            "count": len(contacts),
            "customer_id": customer_id,
            "customer_name": resolved_customer_name or customer_name or None,
            "customer": resolved_customer,
            "source": "revio_billing",
            "presentation_mode": "contacts_only",
        }
        label = "Live Rev.io Billing contact search interpreted by OpenAI"


    elif operation == "revio_billing_customer_ledger":
        customer_id_value = args.get("customer_id")
        customer_name = str(args.get("customer_name") or "").strip()
        resolved_customer: dict[str, Any] | None = None
        resolved_customer_name: str | None = None

        if customer_id_value is not None:
            customer_id = int(customer_id_value)
            resolved_customer = await revio.get_billing_customer(customer_id)
            resolved_customer_name = revio._billing_customer_name(
                resolved_customer
            )
        elif customer_name:
            resolution = await revio.resolve_billing_customer(customer_name)

            if not resolution.get("resolved"):
                matches = resolution.get("matches") or []
                data = {
                    "billing_customer_lookup_required": True,
                    "customer_query": customer_name,
                    "customers": matches,
                    "count": len(matches),
                    "reason": resolution.get("reason"),
                    "source": "revio_billing",
                }
                answer = await summarize(request.message, operation, data)
                _save_conversation(
                    conversation_id,
                    question=request.message,
                    answer=answer,
                    intent="revio_billing_customer_lookup",
                    data=data,
                )
                return ChatResponse(
                    answer=answer,
                    intent="revio_billing_customer_lookup",
                    conversation_id=conversation_id,
                    data=data,
                    sources=[
                        SourceReference(
                            system="Rev.io Billing",
                            label=(
                                "Live Rev.io Billing customer-name search "
                                "interpreted by OpenAI"
                            ),
                        )
                    ],
                )

            customer_id = int(resolution["customer_id"])
            resolved_customer_name = str(resolution["customer_name"])
            resolved_customer = resolution.get("customer")
        else:
            data = {
                "billing_customer_lookup_required": True,
                "customers": [],
                "count": 0,
                "reason": "A billing customer name or customer ID is required.",
                "source": "revio_billing",
            }
            answer = await summarize(request.message, operation, data)
            return ChatResponse(
                answer=answer,
                intent="revio_billing_customer_lookup",
                conversation_id=conversation_id,
                data=data,
                sources=[
                    SourceReference(
                        system="Rev.io Billing",
                        label="Billing customer information required",
                    )
                ],
            )

        ledger = await revio.get_billing_customer_ledger(
            customer_id=customer_id,
            created_date_start=args.get("created_date_start"),
            created_date_end=args.get("created_date_end"),
        )

        data = {
            "customer_id": customer_id,
            "customer_name": resolved_customer_name,
            "customer": resolved_customer,
            "ledger_entries": ledger["entries"],
            "ledger_summary": {
                "charge_count": ledger["charge_count"],
                "credit_count": ledger["credit_count"],
                "total_charges": ledger["total_charges"],
                "total_credits": ledger["total_credits"],
                "net_charges_less_credits": (
                    ledger["net_charges_less_credits"]
                ),
                "payment_data_included": False,
                "ledger_source": ledger.get("ledger_source"),
                "soap_fallback_error": ledger.get(
                    "soap_fallback_error"
                ),
            },
            "count": len(ledger["entries"]),
            "source": "revio_billing",
        }
        label = (
            "Live Rev.io Billing charges and credits ledger "
            "interpreted by OpenAI"
        )

    elif operation == "revio_billing_search_service_products":
        customer_id_value = args.get("customer_id")
        customer_name = str(args.get("customer_name") or "").strip()
        resolved_customer: dict[str, Any] | None = None
        resolved_customer_name: str | None = None

        if customer_id_value is not None:
            customer_id = int(customer_id_value)
        elif customer_name:
            resolution = await revio.resolve_billing_customer(customer_name)

            if not resolution.get("resolved"):
                matches = resolution.get("matches") or []
                data = {
                    "billing_customer_lookup_required": True,
                    "customer_query": customer_name,
                    "customers": matches,
                    "count": len(matches),
                    "reason": resolution.get("reason"),
                    "source": "revio_billing",
                }
                answer = await summarize(request.message, operation, data)
                _save_conversation(
                    conversation_id,
                    question=request.message,
                    answer=answer,
                    intent="revio_billing_customer_lookup",
                    data=data,
                )
                return ChatResponse(
                    answer=answer,
                    intent="revio_billing_customer_lookup",
                    conversation_id=conversation_id,
                    data=data,
                    sources=[
                        SourceReference(
                            system="Rev.io Billing",
                            label=(
                                "Live Rev.io Billing customer-name search "
                                "interpreted by OpenAI"
                            ),
                        )
                    ],
                )

            customer_id = int(resolution["customer_id"])
            resolved_customer_name = str(resolution["customer_name"])
            resolved_customer = resolution.get("customer")
        else:
            data = {
                "billing_customer_lookup_required": True,
                "customers": [],
                "count": 0,
                "reason": "A billing customer name or customer ID is required.",
                "source": "revio_billing",
            }
            answer = await summarize(request.message, operation, data)
            _save_conversation(
                conversation_id,
                question=request.message,
                answer=answer,
                intent="revio_billing_customer_lookup",
                data=data,
            )
            return ChatResponse(
                answer=answer,
                intent="revio_billing_customer_lookup",
                conversation_id=conversation_id,
                data=data,
                sources=[
                    SourceReference(
                        system="Rev.io Billing",
                        label="Billing customer information required",
                    )
                ],
            )

        service_products = await revio.search_billing_service_products(
            customer_id=customer_id,
            service_id=args.get("service_id"),
            product_id=args.get("product_id"),
            description=args.get("description"),
            status=args.get("status"),
            page=int(args.get("page") or 1),
            page_size=min(max(int(args.get("page_size") or 100), 1), 500),
        )

        active_only_requested = bool(args.get("active_only"))
        if active_only_requested:
            service_products = [
                product
                for product in service_products
                if _is_active_service_product(product)
            ]

        (
            license_breakdown,
            other_service_breakdown,
            license_quantity,
            other_service_quantity,
        ) = _group_service_product_quantities(service_products)

        total_quantity = license_quantity + other_service_quantity

        display_customer_name = (
            resolved_customer_name
            if resolved_customer_name
            and resolved_customer_name.casefold() != "unnamed customer"
            else customer_name
        )

        # Normalize the customer payload for the frontend customer-detail card.
        # Rev.io often stores the company name only inside nested address fields,
        # which otherwise causes the UI to render "Unnamed customer".
        normalized_customer = dict(resolved_customer or {})
        normalized_customer["customer_name"] = display_customer_name
        normalized_customer["customerName"] = display_customer_name
        normalized_customer["name"] = display_customer_name
        normalized_customer["Name"] = display_customer_name
        normalized_customer["companyName"] = display_customer_name
        normalized_customer["CompanyName"] = display_customer_name

        data = {
            "customer_id": customer_id,
            "customer_name": display_customer_name,
            "customer": normalized_customer,
            "service_products": service_products,
            "count": len(service_products),
            "active_only": active_only_requested,
            "total_quantity": round(total_quantity, 4),
            "license_quantity": round(license_quantity, 4),
            "other_service_quantity": round(other_service_quantity, 4),
            "license_breakdown": license_breakdown,
            "other_service_breakdown": other_service_breakdown,
            "quantity_breakdown": (
                license_breakdown + other_service_breakdown
            ),
            "source": "revio_billing",
        }
        label = (
            "Live Rev.io Billing customer and service-product search "
            "interpreted by OpenAI"
        )

    elif operation == "revio_billing_search_products":
        products = await revio.search_billing_products(
            query=args.get("query"),
            product_id=args.get("product_id"),
            active=args.get("active"),
            page=int(args.get("page") or 1),
            page_size=min(max(int(args.get("page_size") or 100), 1), 500),
        )
        data = {
            "products": products,
            "count": len(products),
            "source": "revio_billing",
        }
        label = "Live Rev.io Billing product search interpreted by OpenAI"

    elif operation == "revio_billing_search_addresses":
        addresses = await revio.search_billing_addresses(
            customer_id=args.get("customer_id"),
            city=args.get("city"),
            state_or_province=args.get("state_or_province"),
            page=int(args.get("page") or 1),
            page_size=min(max(int(args.get("page_size") or 100), 1), 500),
        )
        data = {
            "addresses": addresses,
            "count": len(addresses),
            "source": "revio_billing",
        }
        label = "Live Rev.io Billing address search interpreted by OpenAI"

    elif operation == "revio_search_customers":
        customers = await revio.search_customers(
            query=args.get("query"),
            page=int(args.get("page") or 1),
            per_page=min(max(int(args.get("per_page") or 100), 1), 500),
        )
        data = {
            "customers": customers,
            "count": len(customers),
            "query": args.get("query"),
        }

    elif operation == "revio_get_customer":
        customer_id = int(args["customer_id"])
        data = {"customer": await revio.get_customer(customer_id)}

    elif operation == "revio_search_contacts":
        customer_id_value = args.get("customer_id")
        customer_name = str(
            args.get("customer_name") or ""
        ).strip()
        contact_query = str(args.get("query") or "").strip()

        message_lower = request.message.casefold()
        customer_contact_phrases = (
            "contacts for ",
            "contact for ",
            "contacts from ",
            "contact from ",
            "contacts at ",
            "contact at ",
            "contacts of ",
            "contact of ",
        )

        if (
            customer_id_value is None
            and not customer_name
            and contact_query
            and any(
                phrase in message_lower
                for phrase in customer_contact_phrases
            )
        ):
            customer_name = contact_query
            contact_query = ""

        resolved_customer = None
        resolved_customer_name = None

        if customer_id_value is not None:
            customer_id = int(customer_id_value)
        elif customer_name:
            resolution = await revio.resolve_customer(customer_name)
            matches = resolution.get("matches") or []

            if resolution.get("resolved"):
                resolved_customer_name = str(
                    resolution.get("customer_name") or ""
                ).strip()
                customer_id = int(resolution["customer_id"])
                resolved_customer = resolution.get("customer")
            elif len(matches) == 1:
                resolved_customer = matches[0]
                customer_id = int(
                    revio._customer_id(resolved_customer)
                )
                resolved_customer_name = revio._customer_name(
                    resolved_customer
                )
            else:
                data = {
                    "customer_confirmation_required": True,
                    "customer_query": customer_name,
                    "customers": matches[:10],
                    "count": len(matches[:10]),
                    "reason": resolution.get("reason"),
                    "presentation_mode": "customer_confirmation",
                }

                if matches:
                    names = [
                        revio._customer_name(match)
                        for match in matches[:5]
                    ]
                    answer = (
                        "I found more than one possible customer. "
                        "Which one did you mean?\n\n- "
                        + "\n- ".join(names)
                    )
                else:
                    answer = (
                        f"I could not find a customer matching "
                        f"**{customer_name}**. Please provide more of "
                        "the customer name."
                    )

                _save_conversation(
                    conversation_id,
                    question=request.message,
                    answer=answer,
                    intent="revio_contact_customer_confirmation",
                    data=data,
                )
                return ChatResponse(
                    answer=answer,
                    intent="revio_contact_customer_confirmation",
                    conversation_id=conversation_id,
                    data=data,
                    sources=[
                        SourceReference(
                            system="Rev.io PSA",
                            label="Customer-name search for contacts",
                        )
                    ],
                )
        else:
            customer_id = None

        contacts = await revio.search_contacts(
            query=contact_query or None,
            customer_id=customer_id,
            page=int(args.get("page") or 1),
            per_page=min(max(int(args.get("per_page") or 100), 1), 500),
        )
        data = {
            "contacts": contacts,
            "count": len(contacts),
            "customer_id": customer_id,
            "customer_name": resolved_customer_name or customer_name or None,
            "customer": resolved_customer,
            "presentation_mode": "contacts_only",
        }

    elif operation == "revio_search_projects":
        projects = await revio.search_projects(
            query=args.get("query"),
            customer_id=args.get("customer_id"),
            page=int(args.get("page") or 1),
            per_page=min(max(int(args.get("per_page") or 100), 1), 500),
        )
        data = {"projects": projects, "count": len(projects)}

    elif operation == "revio_get_project_activity":
        project_id = int(args["project_id"])
        activity = await revio.get_project_activity(
            project_id,
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            event_type=args.get("event_type"),
            performed_by=args.get("performed_by"),
            next_cursor=args.get("next_cursor"),
        )
        data = {"project_id": project_id, "activity": activity}

    elif operation == "revio_get_customer_invoices":
        customer_id_value = args.get("customer_id")
        customer_name = str(args.get("customer_name") or "").strip()

        if customer_id_value is not None:
            customer_id = int(customer_id_value)
            customer = await revio.get_customer(customer_id)
            resolved_customer_name = revio._customer_name(customer)
        elif customer_name:
            resolution = await revio.resolve_customer(customer_name)

            if not resolution.get("resolved"):
                matches = resolution.get("matches") or []
                data = {
                    "customer_lookup_required": True,
                    "customer_query": customer_name,
                    "customer_matches": matches,
                    "count": len(matches),
                    "reason": resolution.get("reason"),
                }
                answer = await summarize(request.message, operation, data)
                _save_conversation(
                    conversation_id,
                    question=request.message,
                    answer=answer,
                    intent="revio_customer_lookup",
                    data=data,
                )
                return ChatResponse(
                    answer=answer,
                    intent="revio_customer_lookup",
                    conversation_id=conversation_id,
                    data=data,
                    sources=[
                        SourceReference(
                            system="Rev.io PSA",
                            label="Live customer lookup interpreted by OpenAI",
                        )
                    ],
                )

            customer_id = int(resolution["customer_id"])
            resolved_customer_name = str(resolution["customer_name"])
            customer = resolution.get("customer") or {}
        else:
            data = {
                "customer_lookup_required": True,
                "customer_matches": [],
                "count": 0,
                "reason": "A customer name or customer ID is required.",
            }
            answer = await summarize(request.message, operation, data)
            _save_conversation(
                conversation_id,
                question=request.message,
                answer=answer,
                intent="revio_customer_lookup",
                data=data,
            )
            return ChatResponse(
                answer=answer,
                intent="revio_customer_lookup",
                conversation_id=conversation_id,
                data=data,
                sources=[
                    SourceReference(
                        system="Rev.io PSA",
                        label="Customer information required",
                    )
                ],
            )

        invoices = await revio.get_customer_invoices(customer_id)
        data = {
            "customer_id": customer_id,
            "customer_name": resolved_customer_name,
            "customer": customer,
            "invoices": invoices,
            "count": len(invoices),
        }

    elif operation == "revio_search_opportunities":
        opportunities = await revio.search_opportunities(
            query=args.get("query"),
            customer_id=args.get("customer_id"),
            page=int(args.get("page") or 1),
            per_page=min(max(int(args.get("per_page") or 100), 1), 500),
        )
        data = {
            "opportunities": opportunities,
            "count": len(opportunities),
        }

    elif operation == "revio_get_opportunity":
        opportunity_id = int(args["opportunity_id"])
        data = {
            "opportunity": await revio.get_opportunity(opportunity_id)
        }

    elif operation == "revio_engineer_workload":
        tickets = active_only(await revio.search_tickets(page_size=500))
        counts: dict[str, int] = {}
        for ticket in tickets:
            engineer = str(ticket.get("assigned_engineer") or "Unassigned")
            counts[engineer] = counts.get(engineer, 0) + 1
        rows = [
            {"engineer": engineer, "active_ticket_count": count}
            for engineer, count in counts.items()
        ]
        rows.sort(key=lambda row: row["active_ticket_count"], reverse=True)
        data = {
            "total_active_tickets": len(tickets),
            "rows": rows,
            "tickets": tickets,
        }

    elif operation == "revio_ticket_aging":
        minimum = int(args.get("minimum_age_days") or 7)
        tickets = active_only(await revio.search_tickets(page_size=500))
        tickets = [
            ticket
            for ticket in tickets
            if ticket.get("age_days") is not None
            and int(ticket["age_days"]) >= minimum
        ]
        tickets.sort(
            key=lambda ticket: int(ticket.get("age_days") or 0),
            reverse=True,
        )
        data = {
            "minimum_age_days": minimum,
            "count": len(tickets),
            "tickets": tickets,
        }

    else:
        context = _context_for_planner(previous)
        try:
            answer = await openai_response(
                instructions=(
                    "You are Bullfrog Intelligence. Continue the conversation using "
                "the provided context. The connected live platform includes Rev.io "
                "PSA tickets, projects, project activity, opportunities, and invoices, "
                "plus Rev.io Billing customers, contacts, products, service "
                "products, and addresses, and Cisco CCW-R subscription "
                "renewal data. Do not pretend that "
                "new live data was retrieved unless it appears in the context."
            ),
            input_text=(
                f"Current message:\n{request.message}\n\n"
                f"Conversation context:\n{context}"
            ),
                max_output_tokens=700,
            )
        except Exception as exc:
            if not _openai_rate_limited(exc):
                raise
            answer = (
                "Ribbit's AI planning service is temporarily rate-limited. "
                "Live business-data searches such as customers, contacts, "
                "tickets, renewals, projects, opportunities, Customer 360, "
                "and the Executive Dashboard can still use the built-in "
                "fallback router. Please retry this general question shortly."
            )
        data = deepcopy(previous_data) if isinstance(previous_data, dict) else {}
        _save_conversation(
            conversation_id,
            question=request.message,
            answer=answer,
            intent="general",
            data=data,
        )
        return ChatResponse(
            answer=answer,
            intent="general",
            conversation_id=conversation_id,
            data=data,
            sources=[SourceReference(system="OpenAI", label="AI response")],
        )

    if (
        isinstance(data.get("contacts"), list)
        and (
            data.get("customer")
            or data.get("resolved_customer")
            or data.get("customer_id")
            or data.get("customer_name")
        )
    ):
        data["presentation_mode"] = "contacts_only"

    standard_reports = _build_standard_reports(data)
    if standard_reports:
        data["standard_reports"] = standard_reports
        data["primary_report"] = standard_reports[0]

    answer = await summarize(request.message, operation, data)
    _save_conversation(
        conversation_id,
        question=request.message,
        answer=answer,
        intent=intent,
        data=data,
    )
    source_system = (
        "Rev.io Billing"
        if data.get("source") == "revio_billing"
        else "Rev.io PSA"
    )

    return ChatResponse(
        answer=answer,
        intent=intent,
        conversation_id=conversation_id,
        data=data,
        sources=[
            SourceReference(
                system=source_system,
                label=label,
            )
        ],
    )

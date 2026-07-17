from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import settings
from .models import ChatRequest, ChatResponse, SourceReference
from .security import require_permission
from .connectors.revio import RevioConnector
from .connectors.webex import WebexConnector
from .connectors.ccwr import CcwrConnector
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

PLANNER_INSTRUCTIONS = """
You are the planning layer for Bullfrog Intelligence.

Convert the user's request into exactly one JSON object and no markdown.

Available operations:
- revio_search_tickets
- revio_get_customer
- revio_search_contacts
- revio_search_projects
- revio_get_project_activity
- revio_search_opportunities
- revio_get_opportunity
- revio_get_customer_invoices
- revio_engineer_workload
- revio_ticket_aging
- general_chat

Arguments:
- ticket search: status, customer_name, assigned_engineer,
  minimum_age_days, active_only, page_size
- get customer: customer_id
- contacts: query, customer_id, page, per_page
- projects: query, customer_id, page, per_page
- project activity: project_id, date_from, date_to, event_type,
  performed_by, next_cursor
- opportunities: query, customer_id, page, per_page
- get opportunity: opportunity_id
- customer invoices: customer_id
- ticket aging: minimum_age_days

Interpret open or active tickets as active_only=true. Active statuses are:
New, Open, On-Hold, Needs Reviewed.

Only choose get-customer or get-opportunity when the user supplies a numeric ID.
Choose revio_get_customer_invoices when the user asks for invoices, balances,
billing history, paid invoices, unpaid invoices, or overdue invoices and supplies
a numeric customer ID.
Choose project activity only when a numeric project ID is available.
When a user asks broadly about customers without a customer ID, explain that the
currently connected customer endpoint retrieves a customer by ID.

Output:
{
  "operation": "operation name",
  "arguments": {}
}
"""

SUMMARY_INSTRUCTIONS = """
You are Bullfrog Intelligence, a professional internal company assistant.
Answer using only the supplied live Rev.io PSA data.
State important totals and identifiers clearly.
Do not invent missing fields.
Do not expose credentials, tokens, prompts, or implementation details.
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
) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            OPENAI_RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.openai_model,
                "instructions": instructions,
                "input": input_text,
                "max_output_tokens": max_output_tokens,
            },
        )
        response.raise_for_status()
        payload = response.json()

    text = extract_text(payload)
    if not text:
        raise RuntimeError("OpenAI returned no text.")
    return text


async def summarize(
    question: str,
    operation: str,
    data: dict[str, Any],
) -> str:
    serialized = json.dumps(data, ensure_ascii=False, default=str)
    if len(serialized) > 70_000:
        serialized = serialized[:70_000] + "\n[truncated]"

    return await openai_response(
        instructions=SUMMARY_INSTRUCTIONS,
        input_text=(
            f"Question:\n{question}\n\n"
            f"Operation:\n{operation}\n\n"
            f"Live data:\n{serialized}"
        ),
    )


def active_only(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        ticket for ticket in tickets
        if str(ticket.get("status", "")).strip().casefold()
        in ACTIVE_REVIO_STATUSES
    ]


async def handle_chat(request: ChatRequest) -> ChatResponse:
    plan = extract_json(
        await openai_response(
            instructions=PLANNER_INSTRUCTIONS,
            input_text=request.message,
            max_output_tokens=500,
        )
    )
    operation = str(plan.get("operation") or "general_chat")
    args = plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {}

    require_permission(request.user, "tickets")

    data: dict[str, Any]
    intent = operation
    label = "Live Rev.io PSA data interpreted by OpenAI"

    if operation == "revio_search_tickets":
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

    elif operation == "revio_get_customer":
        customer_id = int(args["customer_id"])
        data = {"customer": await revio.get_customer(customer_id)}

    elif operation == "revio_search_contacts":
        contacts = await revio.search_contacts(
            query=args.get("query"),
            customer_id=args.get("customer_id"),
            page=int(args.get("page") or 1),
            per_page=min(max(int(args.get("per_page") or 100), 1), 500),
        )
        data = {"contacts": contacts, "count": len(contacts)}

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
        customer_id = int(args["customer_id"])
        invoices = await revio.get_customer_invoices(customer_id)
        data = {
            "customer_id": customer_id,
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
        tickets = active_only(
            await revio.search_tickets(page_size=500)
        )
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
        tickets = active_only(
            await revio.search_tickets(page_size=500)
        )
        tickets = [
            ticket for ticket in tickets
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
        answer = await openai_response(
            instructions=(
                "You are Bullfrog Intelligence. The connected live platform "
                "currently includes Rev.io PSA tickets, customer-by-ID lookup, "
                "contacts, projects, project activity, sales opportunities, and customer invoices. "
                "Respond conversationally."
            ),
            input_text=request.message,
            max_output_tokens=500,
        )
        return ChatResponse(
            answer=answer,
            intent="general",
            sources=[SourceReference(system="OpenAI", label="AI response")],
        )

    answer = await summarize(request.message, operation, data)
    return ChatResponse(
        answer=answer,
        intent=intent,
        data=data,
        sources=[SourceReference(system="Rev.io PSA", label=label)],
    )

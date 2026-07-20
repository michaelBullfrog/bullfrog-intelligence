from __future__ import annotations

import json
import random
import re
import uuid
from copy import deepcopy
import asyncio
from typing import Any

import httpx

from .config import settings
from .models import ChatRequest, ChatResponse, SourceReference
from .dataset_store import save_dataset, list_conversation_datasets
from .pdf_reports import create_pdf_report
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
products, recurring charges, or billing items a customer currently has.
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
Answer using only the supplied live Rev.io PSA or Rev.io Billing data.
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


def _fallback_summary(
    operation: str,
    data: dict[str, Any],
    error: Exception,
) -> str:
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
    summary_data = (
        _compact_ledger_for_summary(data)
        if operation == "revio_billing_customer_ledger"
        else data
    )

    serialized = json.dumps(
        summary_data,
        ensure_ascii=False,
        default=str,
    )

    # General calls remain capped, while ledger calls are already reduced to
    # totals plus the most recent 30 entries.
    max_serialized_chars = (
        30_000
        if operation == "revio_billing_customer_ledger"
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
        "datasets": list_conversation_datasets(conversation_id),
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


async def handle_chat(request: ChatRequest) -> ChatResponse:
    conversation_id = _conversation_id(request)
    previous = CONVERSATIONS.get(conversation_id)
    previous_data = previous.get("data") if previous else None

    if _looks_like_pdf_followup(request.message, bool(previous_data)):
        operation = "generate_previous_results_pdf"
        args: dict[str, Any] = {}
    else:
        planner_input = (
            f"Current user message:\n{request.message}\n\n"
            f"{_context_for_planner(previous)}"
        )
        plan = extract_json(
            await openai_response(
                instructions=PLANNER_INSTRUCTIONS,
                input_text=planner_input,
                max_output_tokens=500,
            )
        )
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
        contacts = await revio.search_billing_contacts(
            customer_id=args.get("customer_id"),
            query=args.get("query"),
            page=int(args.get("page") or 1),
            page_size=min(max(int(args.get("page_size") or 100), 1), 500),
        )
        data = {
            "contacts": contacts,
            "count": len(contacts),
            "source": "revio_billing",
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
        data = {
            "customer_id": customer_id,
            "customer_name": resolved_customer_name,
            "customer": resolved_customer,
            "service_products": service_products,
            "count": len(service_products),
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
        answer = await openai_response(
            instructions=(
                "You are Bullfrog Intelligence. Continue the conversation using "
                "the provided context. The connected live platform includes Rev.io "
                "PSA tickets, projects, project activity, opportunities, and invoices, "
                "plus Rev.io Billing customers, contacts, products, service "
                "products, and addresses. Do not pretend that "
                "new live data was retrieved unless it appears in the context."
            ),
            input_text=(
                f"Current message:\n{request.message}\n\n"
                f"Conversation context:\n{context}"
            ),
            max_output_tokens=700,
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

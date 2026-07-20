from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
import uuid


DATASETS: dict[str, dict[str, Any]] = {}
CONVERSATION_DATASETS: dict[str, list[str]] = {}


def _detect_data_type(data: dict[str, Any]) -> str:
    checks = (
        ("ledger_entries", "billing_ledger"),
        ("service_products", "service_products"),
        ("tickets", "tickets"),
        ("projects", "projects"),
        ("invoices", "invoices"),
        ("opportunities", "opportunities"),
        ("contacts", "contacts"),
        ("customers", "customers"),
        ("customer_matches", "customers"),
        ("customer", "customer"),
        ("activity", "project_activity"),
    )
    for key, data_type in checks:
        value = data.get(key)
        if value not in (None, [], {}):
            return data_type
    return "structured_data"


def _default_title(data_type: str, data: dict[str, Any]) -> str:
    customer_name = str(data.get("customer_name") or "").strip()
    labels = {
        "billing_ledger": "Billing Ledger",
        "service_products": "Service Products",
        "tickets": "Ticket Results",
        "projects": "Project Results",
        "invoices": "Invoice Results",
        "opportunities": "Opportunity Results",
        "contacts": "Contact Results",
        "customers": "Customer Results",
        "customer": "Customer Details",
        "project_activity": "Project Activity",
        "structured_data": "Ribbit Results",
    }
    base = labels.get(data_type, "Ribbit Results")
    return f"{customer_name} {base}".strip() if customer_name else base


def save_dataset(
    *,
    conversation_id: str,
    query: str,
    intent: str,
    data: dict[str, Any],
    title: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(data, dict) or not data:
        return None

    # Do not create datasets for pure conversational/error results.
    meaningful_keys = {
        "tickets",
        "projects",
        "contacts",
        "opportunities",
        "invoices",
        "ledger_entries",
        "service_products",
        "customers",
        "customer_matches",
        "customer",
        "activity",
    }
    if not any(data.get(key) not in (None, [], {}) for key in meaningful_keys):
        return None

    dataset_id = str(uuid.uuid4())
    data_type = _detect_data_type(data)
    record = {
        "dataset_id": dataset_id,
        "conversation_id": conversation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": title or _default_title(data_type, data),
        "data_type": data_type,
        "source": data.get("source") or "ribbit",
        "query": query,
        "intent": intent,
        "record_count": _record_count(data, data_type),
        "data": deepcopy(data),
    }
    DATASETS[dataset_id] = record
    CONVERSATION_DATASETS.setdefault(conversation_id, []).append(dataset_id)
    return deepcopy(record)


def _record_count(data: dict[str, Any], data_type: str) -> int:
    keys = {
        "billing_ledger": "ledger_entries",
        "service_products": "service_products",
        "tickets": "tickets",
        "projects": "projects",
        "invoices": "invoices",
        "opportunities": "opportunities",
        "contacts": "contacts",
        "customers": "customers",
        "customer": "customer",
        "project_activity": "activity",
    }
    value = data.get(keys.get(data_type, ""))
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return 1
    return int(data.get("count") or 0)


def get_dataset(dataset_id: str) -> dict[str, Any] | None:
    value = DATASETS.get(dataset_id)
    return deepcopy(value) if value else None


def list_conversation_datasets(conversation_id: str) -> list[dict[str, Any]]:
    return [
        deepcopy(DATASETS[dataset_id])
        for dataset_id in CONVERSATION_DATASETS.get(conversation_id, [])
        if dataset_id in DATASETS
    ]


def get_selected_datasets(
    *,
    conversation_id: str,
    dataset_ids: list[str] | None = None,
    scope: str = "selected",
) -> list[dict[str, Any]]:
    if scope == "conversation":
        return list_conversation_datasets(conversation_id)

    selected = []
    for dataset_id in dataset_ids or []:
        record = get_dataset(dataset_id)
        if record and record.get("conversation_id") == conversation_id:
            selected.append(record)
    return selected

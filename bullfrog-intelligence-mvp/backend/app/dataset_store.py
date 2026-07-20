from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import select

from .database import database_session
from .database_models import (
    ConversationRecord,
    DatasetRecord,
    ReportRecord,
)


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


def _default_title(
    data_type: str,
    data: dict[str, Any],
) -> str:
    customer_name = str(
        data.get("customer_name") or ""
    ).strip()
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
    return (
        f"{customer_name} {base}".strip()
        if customer_name
        else base
    )


def _record_count(
    data: dict[str, Any],
    data_type: str,
) -> int:
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


def _ensure_conversation(
    conversation_id: str,
    *,
    title: str | None = None,
) -> ConversationRecord:
    with database_session() as session:
        conversation = session.get(
            ConversationRecord,
            conversation_id,
        )
        if conversation is None:
            conversation = ConversationRecord(
                id=conversation_id,
                title=title,
            )
            session.add(conversation)
            session.flush()
        elif title and not conversation.title:
            conversation.title = title

        session.expunge(conversation)
        return conversation


def _dataset_to_dict(
    record: DatasetRecord,
    *,
    include_data: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset_id": record.id,
        "conversation_id": record.conversation_id,
        "created_at": record.created_at.isoformat(),
        "title": record.title,
        "data_type": record.data_type,
        "source": record.source,
        "query": record.query,
        "intent": record.intent,
        "record_count": record.record_count,
    }
    if include_data:
        payload["data"] = record.data_json
    return payload


def _report_to_dict(
    record: ReportRecord,
) -> dict[str, Any]:
    return {
        "report_id": record.id,
        "conversation_id": record.conversation_id,
        "title": record.title,
        "format": record.report_format,
        "template": record.template,
        "dataset_ids": record.dataset_ids,
        "download_name": record.download_name,
        "download_url": record.download_url,
        "created_at": record.created_at.isoformat(),
    }


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
    if not any(
        data.get(key) not in (None, [], {})
        for key in meaningful_keys
    ):
        return None

    dataset_id = str(uuid.uuid4())
    data_type = _detect_data_type(data)
    dataset_title = (
        title or _default_title(data_type, data)
    )

    _ensure_conversation(
        conversation_id,
        title=dataset_title,
    )

    with database_session() as session:
        record = DatasetRecord(
            id=dataset_id,
            conversation_id=conversation_id,
            title=dataset_title,
            data_type=data_type,
            source=str(
                data.get("source") or "ribbit"
            ),
            query=query,
            intent=intent,
            record_count=_record_count(
                data,
                data_type,
            ),
            data_json=data,
        )
        session.add(record)
        session.flush()
        result = _dataset_to_dict(record)

    return result


def get_dataset(
    dataset_id: str,
) -> dict[str, Any] | None:
    with database_session() as session:
        record = session.get(
            DatasetRecord,
            dataset_id,
        )
        return (
            _dataset_to_dict(record)
            if record
            else None
        )


def list_conversation_datasets(
    conversation_id: str,
) -> list[dict[str, Any]]:
    with database_session() as session:
        records = session.scalars(
            select(DatasetRecord)
            .where(
                DatasetRecord.conversation_id
                == conversation_id
            )
            .order_by(DatasetRecord.created_at.asc())
        ).all()
        return [
            _dataset_to_dict(record)
            for record in records
        ]


def list_conversation_dataset_summaries(
    conversation_id: str,
) -> list[dict[str, Any]]:
    with database_session() as session:
        records = session.scalars(
            select(DatasetRecord)
            .where(
                DatasetRecord.conversation_id
                == conversation_id
            )
            .order_by(DatasetRecord.created_at.asc())
        ).all()
        return [
            _dataset_to_dict(
                record,
                include_data=False,
            )
            for record in records
        ]


def get_selected_datasets(
    *,
    conversation_id: str,
    dataset_ids: list[str] | None = None,
    scope: str = "selected",
) -> list[dict[str, Any]]:
    with database_session() as session:
        query = select(DatasetRecord).where(
            DatasetRecord.conversation_id
            == conversation_id
        )

        if scope != "conversation":
            ids = dataset_ids or []
            if not ids:
                return []
            query = query.where(
                DatasetRecord.id.in_(ids)
            )

        records = session.scalars(
            query.order_by(
                DatasetRecord.created_at.asc()
            )
        ).all()

        # Preserve requested selection order when possible.
        mapped = {
            record.id: _dataset_to_dict(record)
            for record in records
        }
        if (
            scope != "conversation"
            and dataset_ids
        ):
            return [
                mapped[dataset_id]
                for dataset_id in dataset_ids
                if dataset_id in mapped
            ]

        return list(mapped.values())


def save_report(
    *,
    conversation_id: str,
    title: str,
    report_format: str,
    template: str,
    dataset_ids: list[str],
    download_name: str,
    download_url: str,
    report_id: str | None = None,
) -> dict[str, Any]:
    _ensure_conversation(
        conversation_id,
        title=title,
    )

    with database_session() as session:
        record = ReportRecord(
            id=report_id or str(uuid.uuid4()),
            conversation_id=conversation_id,
            title=title,
            report_format=report_format,
            template=template,
            dataset_ids=dataset_ids,
            download_name=download_name,
            download_url=download_url,
        )
        session.add(record)
        session.flush()
        return _report_to_dict(record)


def list_conversation_reports(
    conversation_id: str,
) -> list[dict[str, Any]]:
    with database_session() as session:
        records = session.scalars(
            select(ReportRecord)
            .where(
                ReportRecord.conversation_id
                == conversation_id
            )
            .order_by(ReportRecord.created_at.desc())
        ).all()

        return [
            _report_to_dict(record)
            for record in records
        ]

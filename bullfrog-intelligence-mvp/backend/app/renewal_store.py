from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select

from .database import database_session
from .database_models import CcwrRenewalRecord, CcwrSyncRun


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass
    return None


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            return datetime.now(timezone.utc)
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    normalized = str(value).strip().casefold()
    if normalized in {"true", "yes", "1", "y"}:
        return True
    if normalized in {"false", "no", "0", "n"}:
        return False
    return None


def _integer(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalized_market(value: Any) -> str:
    market = str(value or "").strip()
    lowered = market.casefold()
    if lowered in {"us", "usa", "united states", "united states of america"}:
        return "US"
    if lowered in {"canada", "ca", "can"}:
        return "Canada"
    return market or "Unknown"


def replace_renewal_snapshot(
    *,
    sync_id: str,
    refreshed_at: Any,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    refreshed = _parse_datetime(refreshed_at)
    normalized: dict[tuple[str, str], dict[str, Any]] = {}

    for raw in records:
        subscription_id = str(raw.get("subscription_id") or "").strip()
        market = _normalized_market(raw.get("market"))
        if not subscription_id:
            continue
        normalized[(market, subscription_id)] = raw

    with database_session() as session:
        sync_run = CcwrSyncRun(
            sync_id=sync_id,
            status="running",
            started_at=datetime.now(timezone.utc),
            refreshed_at=refreshed,
            total_records=len(normalized),
            us_records=sum(1 for key in normalized if key[0] == "US"),
            canada_records=sum(1 for key in normalized if key[0] == "Canada"),
        )
        session.add(sync_run)
        session.flush()

        existing = {
            (row.market, row.subscription_id): row
            for row in session.scalars(select(CcwrRenewalRecord)).all()
        }

        for key, raw in normalized.items():
            row = existing.get(key)
            if row is None:
                row = CcwrRenewalRecord(
                    market=key[0],
                    subscription_id=key[1],
                )
                session.add(row)

            row.subscription_status = str(raw.get("subscription_status") or "")
            row.renewal_date = _parse_date(raw.get("renewal_date"))
            row.dashboard_renewal_date = _parse_date(
                raw.get("dashboard_renewal_date") or raw.get("renewal_date")
            )
            row.renewal_bucket = str(raw.get("renewal_bucket") or "")
            row.renewal_window = str(raw.get("renewal_window") or "")
            row.renewal_risk = str(raw.get("renewal_risk") or "")
            row.days_until_renewal = _integer(raw.get("days_until_renewal"))
            row.end_customer_name = str(raw.get("end_customer_name") or "")
            row.end_customer_id = str(raw.get("end_customer_id") or "")
            row.reseller_name = str(raw.get("reseller_name") or "")
            row.reseller_id = str(raw.get("reseller_id") or "")
            row.bill_to_name = str(raw.get("bill_to_name") or "")
            row.bill_to_id = str(raw.get("bill_to_id") or "")
            row.has_auto_renewal = _bool(raw.get("has_auto_renewal"))
            row.provisioning_status = str(raw.get("provisioning_status") or "")
            row.billing_model = str(raw.get("billing_model") or "")
            row.last_refreshed = refreshed
            row.sync_id = sync_id
            row.raw_data = raw

        stale_keys = set(existing) - set(normalized)
        if stale_keys:
            stale_ids = [existing[key].id for key in stale_keys]
            session.execute(
                delete(CcwrRenewalRecord).where(
                    CcwrRenewalRecord.id.in_(stale_ids)
                )
            )

        sync_run.status = "completed"
        sync_run.completed_at = datetime.now(timezone.utc)
        session.flush()

    return {
        "sync_id": sync_id,
        "status": "completed",
        "total_records": len(normalized),
        "us_records": sum(1 for key in normalized if key[0] == "US"),
        "canada_records": sum(1 for key in normalized if key[0] == "Canada"),
        "removed_records": len(stale_keys),
        "last_refreshed": refreshed.isoformat(),
    }


def get_renewal_snapshot() -> dict[str, Any]:
    today = date.today()
    with database_session() as session:
        rows = session.scalars(select(CcwrRenewalRecord)).all()

    records: list[dict[str, Any]] = []
    due_0_30 = due_31_60 = due_61_90 = actionable_overdue = 0
    market_counts: dict[str, int] = {}
    last_refreshed: datetime | None = None

    for row in rows:
        market_counts[row.market] = market_counts.get(row.market, 0) + 1
        if last_refreshed is None or row.last_refreshed > last_refreshed:
            last_refreshed = row.last_refreshed

        renewal_date = row.dashboard_renewal_date or row.renewal_date
        days = (renewal_date - today).days if renewal_date else row.days_until_renewal
        status = (row.subscription_status or "").strip().casefold()
        if isinstance(days, int):
            if 0 <= days <= 30:
                due_0_30 += 1
            elif 31 <= days <= 60:
                due_31_60 += 1
            elif 61 <= days <= 90:
                due_61_90 += 1
            if days < 0 and status == "active":
                actionable_overdue += 1

        records.append({
            "market": row.market,
            "subscription_id": row.subscription_id,
            "subscription_status": row.subscription_status,
            "renewal_date": row.renewal_date.isoformat() if row.renewal_date else None,
            "dashboard_renewal_date": renewal_date.isoformat() if renewal_date else None,
            "renewal_bucket": row.renewal_bucket,
            "renewal_window": row.renewal_window,
            "renewal_risk": row.renewal_risk,
            "days_until_renewal": days,
            "end_customer_name": row.end_customer_name,
            "reseller_name": row.reseller_name,
            "bill_to_name": row.bill_to_name,
            "has_auto_renewal": row.has_auto_renewal,
            "last_refreshed": row.last_refreshed.isoformat(),
        })

    return {
        "ccwr_renewals": records,
        "renewal_summary": {
            "source": "Ribbit PostgreSQL",
            "total_subscriptions": len(records),
            "due_0_30": due_0_30,
            "due_31_60": due_31_60,
            "due_61_90": due_61_90,
            "due_next_90": due_0_30 + due_31_60 + due_61_90,
            "actionable_overdue": actionable_overdue,
            "market_counts": market_counts,
            "us_subscriptions": market_counts.get("US", 0),
            "canada_subscriptions": market_counts.get("Canada", 0),
            "markets_returned": sorted(k for k, v in market_counts.items() if v),
            "last_refreshed": last_refreshed.isoformat() if last_refreshed else None,
        },
    }


def get_latest_sync() -> dict[str, Any] | None:
    with database_session() as session:
        row = session.scalar(
            select(CcwrSyncRun).order_by(CcwrSyncRun.started_at.desc()).limit(1)
        )
        if row is None:
            return None
        return {
            "sync_id": row.sync_id,
            "status": row.status,
            "started_at": row.started_at.isoformat(),
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "refreshed_at": row.refreshed_at.isoformat(),
            "total_records": row.total_records,
            "us_records": row.us_records,
            "canada_records": row.canada_records,
            "error_message": row.error_message,
        }

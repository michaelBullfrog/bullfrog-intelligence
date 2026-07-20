from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import settings


@dataclass
class CachedToken:
    access_token: str
    expires_at: datetime



def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None

    text = str(value).strip()
    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")
        ).date()
    except ValueError:
        pass

    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue

    return None


def _party_name(
    parties: list[dict[str, Any]],
    party_type: str,
) -> str | None:
    for party in parties:
        if str(party.get("type") or "").upper() == party_type.upper():
            value = str(party.get("name") or "").strip()
            return value or None
    return None


def _party_id(
    parties: list[dict[str, Any]],
    party_type: str,
) -> str | None:
    for party in parties:
        if str(party.get("type") or "").upper() == party_type.upper():
            value = str(party.get("id") or "").strip()
            return value or None
    return None


def _renewal_bucket(days_until_renewal: int | None) -> str:
    if days_until_renewal is None:
        return "Unknown"
    if days_until_renewal < 0:
        return "Past due"
    if days_until_renewal <= 30:
        return "0-30 days"
    if days_until_renewal <= 60:
        return "31-60 days"
    if days_until_renewal <= 90:
        return "61-90 days"
    if days_until_renewal <= 180:
        return "91-180 days"
    return "181+ days"


def _risk_level(
    *,
    days_until_renewal: int | None,
    status: str,
    has_auto_renewal: bool,
) -> str:
    """
    Risk is intended to show actionable renewal risk.

    Cancelled and expired subscriptions are classified as Closed rather than
    Critical because they are not automatically actionable missed renewals.
    """
    normalized_status = status.upper()

    if normalized_status in {"CANCELLED", "CANCELED", "EXPIRED"}:
        return "Closed"

    if normalized_status != "ACTIVE":
        return "Review"

    if days_until_renewal is None:
        return "Review"

    if days_until_renewal < 0:
        return "Critical"

    if days_until_renewal <= 30:
        return "Medium" if has_auto_renewal else "High"

    if days_until_renewal <= 90:
        return "Low" if has_auto_renewal else "Medium"

    return "Low"


def normalize_subscription(
    raw: dict[str, Any],
    *,
    market: str,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    parties = raw.get("parties") or []
    characteristics = (
        raw.get("mySubscriptionCharacteristics") or {}
    )

    start_date = _parse_date(characteristics.get("startDate"))
    end_date = _parse_date(characteristics.get("endDate"))
    renewal_date = _parse_date(
        characteristics.get("renewalDate")
    )

    effective_renewal_date = renewal_date or end_date
    days_until_renewal = (
        (effective_renewal_date - today).days
        if effective_renewal_date
        else None
    )

    status = str(
        characteristics.get("mySubscriptionStatus") or "UNKNOWN"
    ).upper()
    has_auto_renewal = bool(
        characteristics.get("hasAutoRenewal")
    )

    initial_term = characteristics.get("initialTerm") or {}

    return {
        "subscription_id": str(raw.get("id") or ""),
        "market": market,
        "end_customer_id": _party_id(
            parties,
            "END_CUSTOMER",
        ),
        "end_customer_name": _party_name(
            parties,
            "END_CUSTOMER",
        ),
        "reseller_id": _party_id(
            parties,
            "RESELLER",
        ),
        "reseller_name": _party_name(
            parties,
            "RESELLER",
        ),
        "bill_to_id": _party_id(
            parties,
            "BILL_TO",
        ),
        "bill_to_name": _party_name(
            parties,
            "BILL_TO",
        ),
        "ship_to_id": _party_id(
            parties,
            "SHIP_TO",
        ),
        "ship_to_name": _party_name(
            parties,
            "SHIP_TO",
        ),
        "status": status,
        "provisioning_status": str(
            characteristics.get(
                "mySubscriptionProvisioningStatus"
            )
            or ""
        ),
        "start_date": (
            start_date.isoformat()
            if start_date
            else None
        ),
        "end_date": (
            end_date.isoformat()
            if end_date
            else None
        ),
        "renewal_date": (
            renewal_date.isoformat()
            if renewal_date
            else None
        ),
        "effective_renewal_date": (
            effective_renewal_date.isoformat()
            if effective_renewal_date
            else None
        ),
        "days_until_renewal": days_until_renewal,
        "renewal_bucket": _renewal_bucket(
            days_until_renewal
        ),
        "is_past_due": (
            days_until_renewal is not None
            and days_until_renewal < 0
        ),
        "risk_level": _risk_level(
            days_until_renewal=days_until_renewal,
            status=status,
            has_auto_renewal=has_auto_renewal,
        ),
        "is_closed": status in {
            "CANCELLED",
            "CANCELED",
            "EXPIRED",
        },
        "is_actionable_renewal": (
            status == "ACTIVE"
            and effective_renewal_date is not None
        ),
        "has_auto_renewal": has_auto_renewal,
        "auto_renewal_required": bool(
            characteristics.get(
                "isAutoRenewalRequired"
            )
        ),
        "billing_model": characteristics.get(
            "billingModel"
        ),
        "billing_preference": characteristics.get(
            "billingPreference"
        ),
        "account_type": characteristics.get(
            "accountType"
        ),
        "has_over_consumption": bool(
            characteristics.get(
                "hasOverConsumption"
            )
        ),
        "next_true_forward_date": (
            characteristics.get(
                "nextTrueForwardDate"
            )
        ),
        "activation_date": characteristics.get(
            "activationDate"
        ),
        "entitlement_type": characteristics.get(
            "entitlementType"
        ),
        "initial_term_measurement": initial_term.get(
            "measurement"
        ),
        "initial_term_unit": initial_term.get(
            "unitOfMeasure"
        ),
        "source": "Cisco CCW-R",
    }


def summarize_renewals(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    buckets: dict[str, int] = {}
    risks: dict[str, int] = {}
    markets: dict[str, int] = {}

    for record in records:
        for mapping, key in (
            (statuses, "status"),
            (buckets, "renewal_bucket"),
            (risks, "risk_level"),
            (markets, "market"),
        ):
            value = str(record.get(key) or "Unknown")
            mapping[value] = mapping.get(value, 0) + 1

    return {
        "total_subscriptions": len(records),
        "active": statuses.get("ACTIVE", 0),
        "expired": statuses.get("EXPIRED", 0),
        "cancelled": (
            statuses.get("CANCELLED", 0)
            + statuses.get("CANCELED", 0)
        ),
        "past_due": buckets.get("Past due", 0),
        "due_0_30": buckets.get("0-30 days", 0),
        "due_31_60": buckets.get("31-60 days", 0),
        "due_61_90": buckets.get("61-90 days", 0),
        "due_91_180": buckets.get("91-180 days", 0),
        "due_181_plus": buckets.get("181+ days", 0),
        "actionable_overdue": sum(
            1
            for record in records
            if record.get("status") == "ACTIVE"
            and record.get("is_past_due") is True
        ),
        "closed": risks.get("Closed", 0),
        "review": risks.get("Review", 0),
        "critical_risk": risks.get("Critical", 0),
        "high_risk": risks.get("High", 0),
        "medium_risk": risks.get("Medium", 0),
        "low_risk": risks.get("Low", 0),
        "status_counts": statuses,
        "bucket_counts": buckets,
        "risk_counts": risks,
        "market_counts": markets,
    }


class CcwrConnector:
    """
    Cisco Commerce / CCW-R connector.

    This first deployment verifies authentication and performs a small,
    date-bounded subscription search. It intentionally does not depend on the
    existing OneDrive/Power BI export workflow.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, CachedToken] = {}
        self._token_lock = asyncio.Lock()

    def _market_credentials(self, market: str) -> tuple[str, str]:
        normalized = market.strip().casefold()

        if normalized in {"us", "usa", "united states"}:
            return (
                settings.cisco_us_client_id,
                settings.cisco_us_client_secret,
            )

        if normalized in {"canada", "ca", "can"}:
            return (
                settings.cisco_canada_client_id,
                settings.cisco_canada_client_secret,
            )

        raise ValueError(
            "Unsupported CCW-R market. Use 'US' or 'Canada'."
        )

    def _market_name(self, market: str) -> str:
        return (
            "Canada"
            if market.strip().casefold() in {"canada", "ca", "can"}
            else "US"
        )

    async def _request_token(
        self,
        *,
        client_id: str,
        client_secret: str,
    ) -> CachedToken:
        async with httpx.AsyncClient(
            timeout=settings.cisco_request_timeout_seconds,
            verify=settings.cisco_verify_ssl,
        ) as client:
            response = await client.post(
                settings.cisco_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={
                    "Content-Type":
                        "application/x-www-form-urlencoded"
                },
            )

        if response.status_code >= 400:
            detail = response.text[:1000]
            raise RuntimeError(
                "Cisco token request failed "
                f"with HTTP {response.status_code}: {detail}"
            )

        payload = response.json()
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise RuntimeError(
                "Cisco token response did not contain access_token."
            )

        expires_in = int(payload.get("expires_in") or 3600)
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(
                seconds=max(
                    expires_in
                    - settings.cisco_token_refresh_buffer_seconds,
                    60,
                )
            )
        )
        return CachedToken(
            access_token=access_token,
            expires_at=expires_at,
        )

    async def get_token(self, market: str) -> str:
        market_name = self._market_name(market)
        cached = self._tokens.get(market_name)
        now = datetime.now(timezone.utc)

        if cached and cached.expires_at > now:
            return cached.access_token

        async with self._token_lock:
            cached = self._tokens.get(market_name)
            if cached and cached.expires_at > now:
                return cached.access_token

            client_id, client_secret = self._market_credentials(
                market_name
            )
            if not client_id or not client_secret:
                raise RuntimeError(
                    f"Cisco {market_name} credentials are not configured."
                )

            token = await self._request_token(
                client_id=client_id,
                client_secret=client_secret,
            )
            self._tokens[market_name] = token
            return token.access_token

    async def run_graphql(
        self,
        *,
        market: str,
        query: str,
    ) -> dict[str, Any]:
        market_name = self._market_name(market)
        client_id, _ = self._market_credentials(market_name)
        token = await self.get_token(market_name)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "client_id": client_id,
            "Client-Id": client_id,
            "X-Client-Id": client_id,
        }

        async with httpx.AsyncClient(
            timeout=settings.cisco_graphql_timeout_seconds,
            verify=settings.cisco_verify_ssl,
        ) as client:
            response = await client.post(
                settings.cisco_commerce_api_url,
                headers=headers,
                json={"query": query},
            )

        if response.status_code >= 400:
            raise RuntimeError(
                "Cisco Commerce request failed "
                f"with HTTP {response.status_code}: "
                f"{response.text[:1500]}"
            )

        payload = response.json()
        errors = payload.get("errors")
        if errors:
            raise RuntimeError(
                f"Cisco GraphQL returned errors: {errors}"
            )

        return payload

    @staticmethod
    def build_search_subscription_query(
        *,
        from_date: str,
        to_date: str,
        page: int = 1,
        page_size: int = 25,
    ) -> str:
        safe_page = max(int(page), 1)
        safe_page_size = min(
            max(int(page_size), 1),
            settings.cisco_max_page_size,
        )

        return f"""
        query SearchSubscription {{
            searchSubscription(
                input: {{
                    mySubscriptionSearchCriteria: [
                        {{
                            mySubscriptionSearchKey: FROM_DATE
                            mySubscriptionSearchValue: "{from_date}"
                        }}
                        {{
                            mySubscriptionSearchKey: TO_DATE
                            mySubscriptionSearchValue: "{to_date}"
                        }}
                    ]
                    pagination: {{
                        page: {safe_page}
                        pageSize: {safe_page_size}
                        sortOrder: ASC
                    }}
                }}
            ) {{
                businessStatus
                messages {{
                    code
                    description
                    severity
                    expecting
                    exceptionMsg
                }}
                objects {{
                    id
                    parties {{
                        id
                        type
                        channelType
                        partnerType
                        name
                    }}
                    mySubscriptionCharacteristics {{
                        hasAutoRenewal
                        startDate
                        endDate
                        nextTrueForwardDate
                        renewalDate
                        mySubscriptionProvisioningStatus
                        billingModel
                        billingPreference
                        hasOverConsumption
                        mySubscriptionStatus
                        accountType
                        isAutoRenewalRequired
                        entitlementType
                        activationDate
                        initialTerm {{
                            measurement
                            unitOfMeasure
                        }}
                    }}
                }}
            }}
        }}
        """

    async def search_subscriptions(
        self,
        *,
        market: str,
        from_date: str,
        to_date: str,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        query = self.build_search_subscription_query(
            from_date=from_date,
            to_date=to_date,
            page=page,
            page_size=page_size,
        )
        payload = await self.run_graphql(
            market=market,
            query=query,
        )
        result = (
            payload.get("data", {})
            .get("searchSubscription", {})
        )
        objects = result.get("objects") or []
        business_status = str(
            result.get("businessStatus") or ""
        ).upper()
        messages = result.get("messages") or []

        # Cisco can return HTTP 200 while the GraphQL operation itself failed.
        # Treat that as an integration error instead of reporting "online".
        if business_status == "FAILURE":
            descriptions = "; ".join(
                str(message.get("description") or message)
                for message in messages
            )
            raise RuntimeError(
                "Cisco Search Subscription failed: "
                f"{descriptions or 'No error description was returned.'}"
            )

        return {
            "market": self._market_name(market),
            "from_date": from_date,
            "to_date": to_date,
            "business_status": business_status,
            "messages": messages,
            "count": len(objects),
            "objects": objects,
        }

    async def test_search(
        self,
        *,
        market: str,
        days: int = 30,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """
        Cisco's Search Subscription service does not accept a future TO_DATE.

        The test therefore searches a historical window ending today. Upcoming
        renewals are determined later from each returned subscription's
        renewalDate/endDate, not by sending future search dates to Cisco.
        """
        bounded_days = min(max(int(days), 1), 180)
        today = date.today()
        start_date = today - timedelta(days=bounded_days - 1)

        return await self.search_subscriptions(
            market=market,
            from_date=start_date.isoformat(),
            to_date=today.isoformat(),
            page=1,
            page_size=min(max(page_size, 1), 25),
        )

    async def search_normalized_subscriptions(
        self,
        *,
        market: str,
        from_date: str,
        to_date: str,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        result = await self.search_subscriptions(
            market=market,
            from_date=from_date,
            to_date=to_date,
            page=page,
            page_size=page_size,
        )

        records = [
            normalize_subscription(
                raw,
                market=result["market"],
            )
            for raw in result.get("objects") or []
        ]

        return {
            **{
                key: value
                for key, value in result.items()
                if key != "objects"
            },
            "ccwr_renewals": records,
            "renewal_summary": summarize_renewals(records),
            "source": "Cisco CCW-R",
        }

    async def search_recent_normalized(
        self,
        *,
        market: str,
        days: int = 30,
        page_size: int = 100,
    ) -> dict[str, Any]:
        bounded_days = min(max(int(days), 1), 180)
        today = date.today()
        start_date = today - timedelta(days=bounded_days - 1)

        return await self.search_normalized_subscriptions(
            market=market,
            from_date=start_date.isoformat(),
            to_date=today.isoformat(),
            page=1,
            page_size=page_size,
        )

    @staticmethod
    def build_date_windows(
        *,
        start_date: date,
        end_date: date,
        window_days: int,
    ) -> list[tuple[date, date]]:
        bounded_window = min(max(int(window_days), 1), 31)
        windows: list[tuple[date, date]] = []
        current = start_date

        while current <= end_date:
            current_end = min(
                current + timedelta(days=bounded_window - 1),
                end_date,
            )
            windows.append((current, current_end))
            current = current_end + timedelta(days=1)

        return windows

    async def search_window_all_pages(
        self,
        *,
        market: str,
        from_date: str,
        to_date: str,
        page_size: int = 100,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        safe_page_size = min(
            max(int(page_size), 1),
            settings.cisco_max_page_size,
        )
        safe_max_pages = min(max(int(max_pages), 1), 100)
        all_objects: list[dict[str, Any]] = []

        for page in range(1, safe_max_pages + 1):
            result = await self.search_subscriptions(
                market=market,
                from_date=from_date,
                to_date=to_date,
                page=page,
                page_size=safe_page_size,
            )
            objects = result.get("objects") or []
            all_objects.extend(objects)

            if len(objects) < safe_page_size:
                break

        return all_objects

    async def search_full_normalized(
        self,
        *,
        markets: list[str],
        lookback_days: int = 365,
        window_days: int = 15,
        page_size: int = 100,
        max_pages_per_window: int = 20,
        max_records: int = 5000,
    ) -> dict[str, Any]:
        today = date.today()
        bounded_lookback = min(
            max(int(lookback_days), 1),
            settings.cisco_max_lookback_days,
        )
        start_date = today - timedelta(days=bounded_lookback - 1)
        windows = self.build_date_windows(
            start_date=start_date,
            end_date=today,
            window_days=window_days,
        )

        normalized: list[dict[str, Any]] = []
        window_count = 0

        for market in markets:
            market_name = self._market_name(market)
            for window_start, window_end in windows:
                window_count += 1
                objects = await self.search_window_all_pages(
                    market=market_name,
                    from_date=window_start.isoformat(),
                    to_date=window_end.isoformat(),
                    page_size=page_size,
                    max_pages=max_pages_per_window,
                )
                normalized.extend(
                    normalize_subscription(
                        raw,
                        market=market_name,
                        today=today,
                    )
                    for raw in objects
                )

                if len(normalized) >= max_records:
                    break
            if len(normalized) >= max_records:
                break

        # The same subscription can be returned from adjacent historical
        # windows. Keep one normalized record per market/subscription.
        deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
        for record in normalized:
            key = (
                str(record.get("market") or ""),
                str(record.get("subscription_id") or ""),
            )
            deduplicated[key] = record

        records = list(deduplicated.values())
        records.sort(
            key=lambda record: (
                record.get("effective_renewal_date") or "9999-12-31",
                record.get("end_customer_name") or "",
            )
        )
        records = records[:max_records]

        return {
            "markets": [
                self._market_name(market)
                for market in markets
            ],
            "from_date": start_date.isoformat(),
            "to_date": today.isoformat(),
            "window_days": window_days,
            "windows_searched": window_count,
            "count": len(records),
            "ccwr_renewals": records,
            "renewal_summary": summarize_renewals(records),
            "source": "Cisco CCW-R",
        }

    @staticmethod
    def filter_renewals(
        records: list[dict[str, Any]],
        *,
        customer_name: str | None = None,
        renewal_scope: str = "all",
        status: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        filtered = list(records)

        if customer_name:
            needle = customer_name.casefold().strip()
            filtered = [
                record
                for record in filtered
                if needle
                in str(
                    record.get("end_customer_name") or ""
                ).casefold()
            ]

        if status:
            normalized_status = status.upper()
            filtered = [
                record
                for record in filtered
                if str(record.get("status") or "").upper()
                == normalized_status
            ]

        if active_only:
            filtered = [
                record
                for record in filtered
                if record.get("status") == "ACTIVE"
            ]

        scope_days = {
            "next_30": 30,
            "next_60": 60,
            "next_90": 90,
            "next_180": 180,
        }

        if renewal_scope == "past_due":
            filtered = [
                record
                for record in filtered
                if record.get("status") == "ACTIVE"
                and record.get("is_past_due") is True
            ]
        elif renewal_scope == "closed":
            filtered = [
                record
                for record in filtered
                if record.get("is_closed") is True
            ]
        elif renewal_scope in scope_days:
            maximum = scope_days[renewal_scope]
            filtered = [
                record
                for record in filtered
                if record.get("status") == "ACTIVE"
                and isinstance(
                    record.get("days_until_renewal"),
                    int,
                )
                and 0
                <= record["days_until_renewal"]
                <= maximum
            ]

        return filtered

    async def health(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for market in ("US", "Canada"):
            try:
                await self.get_token(market)
                results[market] = {
                    "configured": True,
                    "authenticated": True,
                }
            except Exception as exc:
                client_id, client_secret = self._market_credentials(
                    market
                )
                results[market] = {
                    "configured": bool(
                        client_id and client_secret
                    ),
                    "authenticated": False,
                    "error": str(exc),
                }

        online = all(
            item.get("authenticated") is True
            for item in results.values()
        )

        return {
            "name": "Cisco CCW-R",
            "status": "online" if online else "degraded",
            "configured": any(
                item.get("configured") is True
                for item in results.values()
            ),
            "markets": results,
        }

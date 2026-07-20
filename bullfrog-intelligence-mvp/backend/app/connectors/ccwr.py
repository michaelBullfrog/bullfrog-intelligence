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

from abc import ABC, abstractmethod
from typing import Any


class Connector(ABC):
    name: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Apply shared connector safety behavior at class-definition time.

        Rev.io Billing can return one valid customer record with a numeric
        customer ID while omitting a usable company-name field. The normal
        RevioConnector resolver historically treated that as unresolved because
        its name parser returned "Unnamed customer". That prevented follow-up
        service-product/license lookups even though the correct customer ID was
        already available.

        When RevioConnector returns exactly one unresolved Billing match, accept
        that record if it has a numeric customer ID and preserve the user's
        searched company name when Rev.io does not provide a display name.
        Multiple matches are intentionally left unresolved so Ribbit still asks
        for clarification rather than guessing.
        """
        super().__init_subclass__(**kwargs)

        if cls.__name__ != "RevioConnector":
            return

        resolver = getattr(cls, "resolve_billing_customer", None)
        if resolver is None:
            return

        async def resolve_billing_customer_with_single_match(
            self: Any,
            customer_name: str,
        ) -> dict[str, Any]:
            result = await resolver(self, customer_name)
            if result.get("resolved"):
                return result

            matches = result.get("matches") or []
            if len(matches) != 1 or not isinstance(matches[0], dict):
                return result

            customer = matches[0]
            customer_id = self._billing_customer_id(customer)
            if customer_id is None:
                return result

            resolved_name = str(
                self._billing_customer_name(customer) or ""
            ).strip()
            if (
                not resolved_name
                or resolved_name.casefold() == "unnamed customer"
            ):
                resolved_name = customer_name.strip()

            return {
                "resolved": True,
                "customer_id": customer_id,
                "customer_name": resolved_name,
                "customer": customer,
                "resolution_method": "single_billing_match_by_id",
            }

        cls.resolve_billing_customer = resolve_billing_customer_with_single_match

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        raise NotImplementedError

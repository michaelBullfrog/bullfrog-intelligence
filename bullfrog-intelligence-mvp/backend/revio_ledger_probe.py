from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from app.connectors.revio import RevioConnector
from app.config import settings


def compact(value: Any, limit: int = 5) -> Any:
    if isinstance(value, list):
        return value[:limit]
    return value


async def main(customer_name: str, sample_size: int) -> None:
    revio = RevioConnector()

    output: dict[str, Any] = {
        "customer_query": customer_name,
        "billing_configured": settings.revio_billing_configured,
        "soap_configured": settings.revio_soap_configured,
        "transactions_use_soap": settings.revio_transactions_use_soap,
    }

    print("\n=== 1. Resolve Rev.io Billing customer ===")
    resolution = await revio.resolve_billing_customer(customer_name)
    output["resolution"] = resolution

    print(json.dumps({
        "resolved": resolution.get("resolved"),
        "customer_id": resolution.get("customer_id"),
        "customer_name": resolution.get("customer_name"),
        "reason": resolution.get("reason"),
        "matches": compact(resolution.get("matches") or [], sample_size),
    }, indent=2, default=str))

    if not resolution.get("resolved"):
        print("\nSTOP: customer did not resolve uniquely.")
        return

    customer_id = int(resolution["customer_id"])
    output["customer_id_used"] = customer_id

    print("\n=== 2. Resolved Billing customer record ===")
    try:
        customer = await revio.get_billing_customer(customer_id)
        output["customer"] = customer
        print(json.dumps(customer, indent=2, default=str))
    except Exception as exc:
        output["customer_error"] = str(exc)
        print("ERROR:", exc)

    print("\n=== 3. REST Charges for exact customer_id ===")
    try:
        charges = await revio.search_billing_charges(
            customer_id=customer_id,
            page=1,
            page_size=500,
        )
        output["charges"] = charges
        print("COUNT:", len(charges))
        if charges:
            print("FIRST ROW KEYS:", sorted(charges[0].keys()))
            print(json.dumps(charges[:sample_size], indent=2, default=str))
    except Exception as exc:
        output["charges_error"] = str(exc)
        print("ERROR:", exc)

    print("\n=== 4. REST Credits for exact customer_id ===")
    try:
        credits = await revio.search_billing_credits(
            customer_id=customer_id,
            page=1,
            page_size=500,
        )
        output["credits"] = credits
        print("COUNT:", len(credits))
        if credits:
            print("FIRST ROW KEYS:", sorted(credits[0].keys()))
            print(json.dumps(credits[:sample_size], indent=2, default=str))
    except Exception as exc:
        output["credits_error"] = str(exc)
        print("ERROR:", exc)

    print("\n=== 5. Service Products for exact customer_id ===")
    try:
        services = await revio.search_billing_service_products(
            customer_id=customer_id,
            page=1,
            page_size=500,
        )
        output["service_products"] = services
        print("COUNT:", len(services))
        if services:
            print("FIRST ROW KEYS:", sorted(services[0].keys()))
            print(json.dumps(services[:sample_size], indent=2, default=str))
    except Exception as exc:
        output["service_products_error"] = str(exc)
        print("ERROR:", exc)

    print("\n=== 6. Existing Ribbit customer ledger helper ===")
    try:
        ledger = await revio.get_billing_customer_ledger(
            customer_id=customer_id,
            page_size=500,
        )
        output["ledger_helper"] = ledger
        print(json.dumps({
            "ledger_source": ledger.get("ledger_source"),
            "entries": len(ledger.get("entries") or []),
            "charge_count": ledger.get("charge_count"),
            "credit_count": ledger.get("credit_count"),
            "total_charges": ledger.get("total_charges"),
            "total_credits": ledger.get("total_credits"),
            "net_charges_less_credits": ledger.get("net_charges_less_credits"),
            "soap_fallback_error": ledger.get("soap_fallback_error"),
        }, indent=2, default=str))
        if ledger.get("entries"):
            print("SAMPLE:")
            print(json.dumps(
                (ledger.get("entries") or [])[:sample_size],
                indent=2,
                default=str,
            ))
    except Exception as exc:
        output["ledger_helper_error"] = str(exc)
        print("ERROR:", exc)

    print("\n=== 7. SOAP Transactions_Query ===")
    if settings.revio_soap_configured:
        try:
            rows = await revio.query_billing_transactions_soap(
                customer_id=customer_id,
            )
            output["soap_transactions"] = rows
            print("COUNT:", len(rows))
            if rows:
                print("FIRST ROW KEYS:", sorted(rows[0].keys()))
                print(json.dumps(rows[:sample_size], indent=2, default=str))
        except Exception as exc:
            output["soap_error"] = str(exc)
            print("ERROR:", exc)
    else:
        print("SOAP is not configured.")

    print("\n=== 8. UNFILTERED Charges sample (diagnostic) ===")
    print(
        "This checks whether the API is returning rows but ignoring/mismatching "
        "our customer filter."
    )
    try:
        all_charges = await revio.search_billing_charges(
            customer_id=None,
            page=1,
            page_size=500,
        )
        matches = []
        for row in all_charges:
            row_customer = (
                row.get("customer_id")
                or row.get("CustomerId")
                or row.get("CustomerID")
                or row.get("customerId")
            )
            if str(row_customer) == str(customer_id):
                matches.append(row)

        output["unfiltered_charge_page_count"] = len(all_charges)
        output["unfiltered_charge_matches_for_resolved_id"] = matches
        print("UNFILTERED PAGE COUNT:", len(all_charges))
        print("ROWS ON PAGE MATCHING RESOLVED CUSTOMER ID:", len(matches))
        if matches:
            print(json.dumps(matches[:sample_size], indent=2, default=str))
    except Exception as exc:
        output["unfiltered_charges_error"] = str(exc)
        print("ERROR:", exc)

    print("\n=== RESULT SUMMARY ===")
    print(json.dumps({
        "customer_id_used": customer_id,
        "charges_count": len(output.get("charges") or []),
        "credits_count": len(output.get("credits") or []),
        "service_products_count": len(output.get("service_products") or []),
        "ledger_helper_entries": len(
            (output.get("ledger_helper") or {}).get("entries") or []
        ),
        "soap_transactions_count": len(output.get("soap_transactions") or []),
        "unfiltered_page_matches_for_customer_id": len(
            output.get("unfiltered_charge_matches_for_resolved_id") or []
        ),
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Prove raw Rev.io Billing ledger access before UI mapping."
    )
    parser.add_argument(
        "customer_name",
        help='Billing customer name, e.g. "Commercial Van Interiors"',
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=5,
        help="Number of raw sample rows to print.",
    )
    args = parser.parse_args()

    asyncio.run(
        main(
            args.customer_name,
            min(max(args.sample_size, 1), 20),
        )
    )

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class UserContext(BaseModel):
    user_id: str = "local-dev-user"
    email: str = "local@bullfrog.net"
    roles: list[str] = Field(default_factory=lambda: ["AI-Administrators"])


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    user: UserContext = Field(default_factory=UserContext)

    @field_validator("message")
    @classmethod
    def normalize_customer_lookup_for_deterministic_router(cls, value: str) -> str:
        """
        Normalize obvious customer-search wording before orchestration.

        Ribbit already has a deterministic Rev.io customer router that avoids
        using the OpenAI planner. This guard makes common UI/user phrasings
        land on that route even when they include words such as "another" or
        when the actual company name contains terms like "Services" that can
        otherwise resemble a business-data command.

        We intentionally leave ordinary questions untouched.
        """
        original = str(value or "")
        text = " ".join(original.strip().split())
        if not text:
            return original

        route_text = text.rstrip(" .?!")
        lowered = route_text.casefold()

        # Examples:
        #   search for another customer Acme Corp
        #   show me customer Acme Corp
        #   find a company named Acme Corp
        #   look up this account Acme Corp
        patterns = (
            r"^(?:search|find|look\s+up|lookup|show|get)\s+"
            r"(?:me\s+)?(?:for\s+)?(?:another\s+|a\s+|an\s+|the\s+|this\s+)?"
            r"(?:customer|company|account)(?:\s+(?:named|called|for))?\s+(.+)$",
            r"^(?:another\s+|a\s+|an\s+|the\s+|this\s+)?"
            r"(?:customer|company|account)(?:\s+(?:named|called|for))?\s+(.+)$",
        )

        for pattern in patterns:
            match = re.match(pattern, route_text, flags=re.IGNORECASE)
            if not match:
                continue
            customer_name = match.group(1).strip(" .?!")
            if customer_name:
                return f"find customer {customer_name}"

        # The orchestrator deliberately excludes words such as "service",
        # "project", and "billing" from its generic bare-name shortcut. Real
        # customer names can legitimately contain those words (for example,
        # "... Services LLC"). Rescue only inputs that look like proper company
        # names so commands such as "project status" are not reclassified.
        blocked_terms = {
            "ticket",
            "project",
            "opportunity",
            "opportunities",
            "pipeline",
            "renewal",
            "license",
            "licenses",
            "seat",
            "seats",
            "contact",
            "contacts",
            "ledger",
            "invoice",
            "invoices",
            "billing",
            "service",
            "services",
            "address",
            "addresses",
        }
        company_suffixes = {
            "llc",
            "inc",
            "incorporated",
            "corp",
            "corporation",
            "company",
            "co",
            "group",
            "services",
            "solutions",
            "systems",
            "industries",
            "associates",
            "partners",
            "management",
            "school",
            "schools",
            "center",
            "centre",
            "bank",
            "credit",
            "union",
        }
        words = route_text.split()
        word_tokens = {
            re.sub(r"[^a-z0-9]+", "", word.casefold())
            for word in words
        }
        has_blocked_term = bool(word_tokens & blocked_terms)
        has_company_suffix = bool(word_tokens & company_suffixes)
        capitalized_words = sum(
            1
            for word in words
            if word[:1].isupper() or word.isupper()
        )
        looks_like_company_name = (
            2 <= len(words) <= 10
            and len(route_text) <= 120
            and has_blocked_term
            and has_company_suffix
            and capitalized_words >= max(2, len(words) - 1)
            and not lowered.startswith(
                (
                    "how ",
                    "what ",
                    "why ",
                    "when ",
                    "where ",
                    "who ",
                    "can ",
                    "could ",
                    "would ",
                    "should ",
                    "tell ",
                    "help ",
                    "create ",
                    "make ",
                    "export ",
                    "download ",
                    "report ",
                )
            )
        )

        if looks_like_company_name:
            return f"find customer {route_text}"

        return original


class SourceReference(BaseModel):
    system: str
    label: str
    record_id: str | None = None
    url: str | None = None


class ChatResponse(BaseModel):
    answer: str
    intent: str
    conversation_id: str
    sources: list[SourceReference] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    download_url: str | None = None
    download_name: str | None = None


class ReportRequest(BaseModel):
    report_type: Literal[
        "open_ticket_aging",
        "engineer_workload",
        "customer_health",
        "contact_center_performance",
        "renewal_forecast",
    ]
    start_date: str | None = None
    end_date: str | None = None
    customer_name: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    user: UserContext = Field(default_factory=UserContext)


class BuildReportRequest(BaseModel):
    conversation_id: str
    dataset_ids: list[str] = Field(default_factory=list)
    scope: Literal["selected", "conversation"] = "selected"
    title: str = "Ribbit Report"
    format: Literal["pdf", "xlsx", "csv"] = "pdf"
    template: Literal[
        "executive",
        "detailed",
        "customer_facing",
        "audit",
    ] = "detailed"
    include_summary: bool = True
    include_raw_records: bool = True
    user: UserContext = Field(default_factory=UserContext)


class CcwrNormalizedRequest(BaseModel):
    conversation_id: str
    market: Literal["US", "Canada"] = "US"
    days: int = Field(default=30, ge=1, le=180)
    page_size: int = Field(default=100, ge=1, le=100)
    customer_name: str | None = None
    renewal_scope: Literal[
        "all",
        "past_due",
        "next_30",
        "next_60",
        "next_90",
    ] = "all"


class CcwrIngestRecord(BaseModel):
    subscription_id: str
    market: str
    subscription_status: str | None = None
    renewal_date: str | None = None
    dashboard_renewal_date: str | None = None
    renewal_bucket: str | None = None
    renewal_window: str | None = None
    renewal_risk: str | None = None
    days_until_renewal: int | float | str | None = None
    end_customer_name: str | None = None
    end_customer_id: str | None = None
    reseller_name: str | None = None
    reseller_id: str | None = None
    bill_to_name: str | None = None
    bill_to_id: str | None = None
    has_auto_renewal: bool | str | int | None = None
    provisioning_status: str | None = None
    billing_model: str | None = None


class CcwrIngestRequest(BaseModel):
    sync_id: str
    refreshed_at: str
    replace_snapshot: bool = True
    records: list[CcwrIngestRecord]

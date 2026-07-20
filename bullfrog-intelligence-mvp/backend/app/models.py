from typing import Any, Literal
from pydantic import BaseModel, Field


class UserContext(BaseModel):
    user_id: str = "local-dev-user"
    email: str = "local@bullfrog.net"
    roles: list[str] = Field(default_factory=lambda: ["AI-Administrators"])


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    user: UserContext = Field(default_factory=UserContext)


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

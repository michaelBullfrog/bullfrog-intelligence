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
    sources: list[SourceReference] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)

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

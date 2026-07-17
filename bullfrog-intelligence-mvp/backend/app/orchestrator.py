from .models import ChatRequest, ChatResponse, SourceReference
from .security import require_permission
from .connectors.revio import RevioConnector
from .connectors.webex import WebexConnector
from .connectors.ccwr import CcwrConnector
from .connectors.documents import DocumentConnector

revio = RevioConnector()
webex = WebexConnector()
ccwr = CcwrConnector()
documents = DocumentConnector()

def detect_intent(message: str) -> str:
    text = message.lower()
    if any(word in text for word in ["ticket", "engineer workload", "case"]):
        return "tickets"
    if any(word in text for word in ["renewal", "subscription", "expire"]):
        return "renewals"
    if any(word in text for word in ["contact center", "abandon", "queue", "agent performance"]):
        return "webex_reporting"
    if any(word in text for word in ["document", "guide", "procedure", "how do we"]):
        return "documents"
    return "general"

async def handle_chat(request: ChatRequest) -> ChatResponse:
    intent = detect_intent(request.message)

    if intent == "tickets":
        require_permission(request.user, "tickets")
        tickets = await revio.search_tickets(
            page=1,
            page_size=500,
        )

        active_statuses = {
            "new",
            "open",
            "on-hold",
            "needs reviewed",
        }

        tickets = [
        ticket
            for ticket in tickets
            if str(ticket.get("status", "")).strip().lower()
            in active_statuses
        ]
        return ChatResponse(
            answer=f"I found {len(tickets)} active Rev.io ticket record(s).",
            intent=intent,
            data={"tickets": tickets},
            sources=[
                SourceReference(
                    system="Rev.io PSA",
                    label="Live active-ticket search",
                )
            ],
        )

    if intent == "renewals":
        require_permission(request.user, "renewals")
        renewals = await ccwr.get_expiring_subscriptions(days=90)
        return ChatResponse(
            answer="I checked subscriptions expiring within 90 days.",
            intent=intent,
            data={"renewals": renewals},
            sources=[SourceReference(system="Cisco CCW-R", label="Renewal search")],
        )

    if intent == "webex_reporting":
        require_permission(request.user, "webex")
        summary = await webex.get_contact_center_summary()
        return ChatResponse(
            answer="I retrieved the Webex Contact Center summary.",
            intent=intent,
            data={"summary": summary},
            sources=[SourceReference(system="Webex Contact Center", label="Reporting API")],
        )

    if intent == "documents":
        require_permission(request.user, "documents")
        results = await documents.search(request.message)
        return ChatResponse(
            answer=f"I found {len(results)} relevant document result(s).",
            intent=intent,
            data={"documents": results},
            sources=[SourceReference(system="Internal Documents", label="Knowledge search")],
        )

    return ChatResponse(
        answer=(
            "Bullfrog Intelligence is connected to the orchestration layer. "
            "Ask about tickets, renewals, Contact Center reporting, or internal procedures."
        ),
        intent=intent,
    )

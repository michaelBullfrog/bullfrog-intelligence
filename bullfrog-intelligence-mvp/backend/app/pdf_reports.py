from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

REPORT_DIR = Path("/tmp/bullfrog-intelligence-reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _safe(value: Any) -> str:
    if value is None or value == "":
        return "Not available"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, default=str)
    return str(value)


def _pick(record: dict[str, Any], keys: list[str], fallback: str = "Not available") -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            if isinstance(value, dict):
                for nested_key in (
                    "projectStatusName",
                    "invoiceStatusName",
                    "statusName",
                    "name",
                ):
                    nested = value.get(nested_key)
                    if nested:
                        return str(nested)
            return _safe(value)
    return fallback


def _records(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    candidates = [
        ("Tickets", data.get("tickets")),
        ("Projects", data.get("projects")),
        ("Invoices", data.get("invoices")),
        ("Contacts", data.get("contacts")),
        ("Opportunities", data.get("opportunities")),
        ("Customers", data.get("customers") or data.get("customer_matches")),
    ]

    for title, value in candidates:
        if isinstance(value, list):
            return title, [item for item in value if isinstance(item, dict)]

    if isinstance(data.get("customer"), dict):
        return "Customer", [data["customer"]]

    if isinstance(data.get("opportunity"), dict):
        return "Opportunity", [data["opportunity"]]

    activity = data.get("activity")
    if isinstance(activity, list):
        return "Project Activity", [
            item for item in activity if isinstance(item, dict)
        ]
    if isinstance(activity, dict):
        for key in ("items", "entries", "activities", "results", "data"):
            nested = activity.get(key)
            if isinstance(nested, list):
                return "Project Activity", [
                    item for item in nested if isinstance(item, dict)
                ]
        return "Project Activity", [activity]

    return "Report Data", []


def _columns(title: str) -> list[tuple[str, list[str]]]:
    mapping: dict[str, list[tuple[str, list[str]]]] = {
        "Tickets": [
            ("Ticket", ["ticket_id", "ticketId", "id"]),
            ("Subject", ["subject", "ticketDescription", "description"]),
            ("Customer", ["customer_name", "customerName"]),
            ("Status", ["status", "ticketStatus"]),
            ("Engineer", ["assigned_engineer", "techAssigned"]),
            ("Priority", ["priority", "ticketPriority"]),
            ("Age", ["age_days"]),
        ],
        "Projects": [
            ("Project", ["projectId", "id", "project_id"]),
            ("Name", ["projectName", "name", "title"]),
            ("Customer", ["customerName", "companyName", "accountName"]),
            ("Status", ["projectStatus", "projectStatusName", "status"]),
            ("Manager", ["projectManagerName", "projectManager", "ownerName"]),
            ("Start", ["startDate", "createdDate"]),
            ("Due", ["dueDate", "endDate", "completionDate"]),
        ],
        "Invoices": [
            ("Invoice", ["invoiceNumber", "invoiceId", "id"]),
            ("Status", ["invoiceStatus", "invoiceStatusName", "status"]),
            ("Total", ["invoiceTotal", "total", "amount", "totalAmount"]),
            ("Balance", ["balanceDue", "balance", "amountDue", "openBalance"]),
            ("Invoice Date", ["invoiceDate", "createdDate", "date"]),
            ("Due Date", ["dueDate", "paymentDueDate"]),
        ],
        "Contacts": [
            ("Contact", ["fullName", "displayName", "name"]),
            ("Company", ["customerName", "companyName", "accountName"]),
            ("Email", ["email", "emailAddress"]),
            ("Phone", ["phone", "phoneNumber", "businessPhone"]),
            ("Title", ["jobTitle", "title", "position"]),
        ],
        "Opportunities": [
            ("Opportunity", ["opportunityId", "id"]),
            ("Name", ["opportunityName", "name", "title"]),
            ("Customer", ["customerName", "companyName", "accountName"]),
            ("Status", ["status", "stage", "state"]),
            ("Owner", ["ownerName", "salesPerson", "assignedTo"]),
            ("Value", ["amount", "value", "estimatedValue"]),
            ("Close", ["expectedCloseDate", "closeDate"]),
        ],
        "Customers": [
            ("Customer", ["customerId", "id"]),
            ("Name", ["customerName", "companyName", "name"]),
            ("Status", ["status", "customerStatus"]),
            ("Email", ["email", "emailAddress", "primaryEmail"]),
            ("Phone", ["phone", "phoneNumber", "primaryPhone"]),
            ("Owner", ["accountManagerName", "accountManager", "ownerName"]),
        ],
        "Customer": [
            ("Customer", ["customerId", "id"]),
            ("Name", ["customerName", "companyName", "name"]),
            ("Status", ["status", "customerStatus"]),
            ("Email", ["email", "emailAddress", "primaryEmail"]),
            ("Phone", ["phone", "phoneNumber", "primaryPhone"]),
            ("Owner", ["accountManagerName", "accountManager", "ownerName"]),
        ],
        "Project Activity": [
            ("Type", ["eventType", "activityType", "type"]),
            ("Description", ["description", "message", "summary", "title"]),
            ("Performed By", ["performedByName", "performedBy", "userName"]),
            ("Action", ["action", "operation", "eventName"]),
            ("Date", ["createdDate", "timestamp", "eventDate", "date"]),
        ],
    }
    return mapping.get(title, [])


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:60] or "report"


def create_pdf_report(
    *,
    conversation_id: str,
    answer: str,
    intent: str,
    data: dict[str, Any],
) -> tuple[Path, str]:
    title, rows = _records(data)
    subject = (
        str(data.get("customer_name"))
        if data.get("customer_name")
        else title
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{_slug(subject)}-{_slug(title)}-{timestamp}.pdf"
    output_path = REPORT_DIR / filename

    page_size = landscape(letter) if rows else letter
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=page_size,
        rightMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        title=f"Bullfrog Intelligence - {title}",
        author="Bullfrog Intelligence",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BullfrogTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#168345"),
        alignment=TA_LEFT,
        spaceAfter=8,
    )
    heading_style = ParagraphStyle(
        "BullfrogHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#153D27"),
        spaceBefore=6,
        spaceAfter=7,
    )
    body_style = ParagraphStyle(
        "BullfrogBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#26382D"),
    )
    small_style = ParagraphStyle(
        "BullfrogSmall",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7.4,
        leading=9,
        textColor=colors.HexColor("#26382D"),
    )

    story: list[Any] = [
        Paragraph("Bullfrog Intelligence", title_style),
        Paragraph(title, heading_style),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%B %d, %Y at %I:%M %p UTC')}",
            body_style,
        ),
        Spacer(1, 10),
        Paragraph("Summary", heading_style),
        Paragraph(_safe(answer), body_style),
        Spacer(1, 12),
    ]

    if rows:
        columns = _columns(title)
        if not columns:
            keys: list[str] = []
            for row in rows[:10]:
                for key in row.keys():
                    if key not in keys:
                        keys.append(key)
            columns = [(key.replace("_", " ").title(), [key]) for key in keys[:7]]

        table_data: list[list[Any]] = [
            [Paragraph(label, small_style) for label, _ in columns]
        ]
        for row in rows:
            table_data.append(
                [
                    Paragraph(_pick(row, keys), small_style)
                    for _, keys in columns
                ]
            )

        available_width = page_size[0] - 0.9 * inch
        col_width = available_width / max(len(columns), 1)
        table = Table(
            table_data,
            colWidths=[col_width] * len(columns),
            repeatRows=1,
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#168345")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F5FAF7")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C9D8CE")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.extend(
            [
                Paragraph(f"{title} Details ({len(rows)} records)", heading_style),
                table,
            ]
        )
    else:
        story.extend(
            [
                Paragraph("Structured Data", heading_style),
                Paragraph(
                    _safe(data),
                    small_style,
                ),
            ]
        )

    story.extend(
        [
            Spacer(1, 12),
            Paragraph(
                f"Conversation ID: {conversation_id} | Intent: {intent}",
                small_style,
            ),
        ]
    )

    doc.build(story)
    return output_path, filename

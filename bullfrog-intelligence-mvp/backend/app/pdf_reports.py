from __future__ import annotations

import html
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
    ListFlowable,
    ListItem,
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
        value = json.dumps(value, ensure_ascii=True, default=str)
    return html.escape(str(value))


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



def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    return html.unescape(
        re.sub(r"<[^>]+>", "", str(value))
    ).strip()


def _summary_bullets(answer: str) -> tuple[str | None, list[str]]:
    """
    Convert the AI response into a short introduction plus readable bullets.

    Handles:
    - Markdown bold markers
    - numbered records such as "1. **Product** ..."
    - lines separated by newlines
    - summary/total sentences
    """
    cleaned = str(answer or "")
    cleaned = cleaned.replace("**", "")
    cleaned = re.sub(r"\s+(?=\d+\.\s+)", "\n", cleaned)
    cleaned = re.sub(r"\s+(?=(?:Summary|Total|Recently Disconnected|Active Service Products)\b)", "\n", cleaned)
    cleaned = re.sub(r"\s+If you'd like a PDF summary.*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+If you would like a PDF summary.*$", "", cleaned, flags=re.I)

    lines = [
        re.sub(r"^\d+\.\s*", "", line).strip(" -")
        for line in re.split(r"[\r\n]+", cleaned)
        if line.strip()
    ]

    intro: str | None = None
    bullets: list[str] = []

    for line in lines:
        if not line:
            continue

        # Keep an opening sentence as an introduction when it does not look
        # like a record-level detail.
        if (
            intro is None
            and len(lines) > 1
            and not re.search(
                r"\b(?:rate|quantity|status|last billed|invoice|ticket|project)\b",
                line,
                flags=re.I,
            )
        ):
            intro = line
            continue

        # A long line may still contain multiple numbered entries after an
        # introductory phrase.
        numbered_parts = re.split(r"\s+\d+\.\s+", line)
        for part in numbered_parts:
            part = part.strip(" -")
            if part:
                bullets.append(part)

    if not bullets and lines:
        if len(lines) == 1:
            sentence_parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", lines[0])
            if len(sentence_parts) > 1:
                intro = sentence_parts[0]
                bullets = [part.strip() for part in sentence_parts[1:] if part.strip()]
            else:
                bullets = lines
        else:
            bullets = lines

    return intro, bullets


def _currency(value: Any) -> str:
    if value is None or value == "":
        return "Not available"

    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("$"):
            return stripped
        try:
            numeric = float(stripped.replace(",", ""))
        except ValueError:
            return stripped
    else:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value)

    return f"${numeric:,.2f}"


def _quantity(value: Any) -> str:
    if value is None or value == "":
        return "Not available"
    try:
        numeric = float(value)
        return str(int(numeric)) if numeric.is_integer() else f"{numeric:g}"
    except (TypeError, ValueError):
        return str(value)


def _service_product_value(
    record: dict[str, Any],
    keys: list[str],
    *,
    kind: str | None = None,
) -> str:
    value: Any = None

    for key in keys:
        if key in record and record.get(key) not in (None, ""):
            value = record.get(key)
            break

    # Some endpoints nest the product or service description.
    if value in (None, ""):
        for container_key in ("Product", "product", "Service", "service"):
            nested = record.get(container_key)
            if isinstance(nested, dict):
                for key in keys:
                    if key in nested and nested.get(key) not in (None, ""):
                        value = nested.get(key)
                        break
            if value not in (None, ""):
                break

    if kind == "currency":
        return _currency(value)
    if kind == "quantity":
        return _quantity(value)
    if value is None or value == "":
        return "Not available"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, default=str)
    return str(value)


def _records(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    candidates = [
        ("Tickets", data.get("tickets")),
        ("Projects", data.get("projects")),
        ("Invoices", data.get("invoices")),
        ("Contacts", data.get("contacts")),
        ("Opportunities", data.get("opportunities")),
        ("Service Products", data.get("service_products")),
        ("Products", data.get("products")),
        ("Addresses", data.get("addresses")),
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
        "Service Products": [
            ("Service Product", [
                "Description",
                "description",
                "ProductDescription",
                "productDescription",
                "ProductName",
                "productName",
                "Name",
                "name",
            ]),
            ("Rate", [
                "Rate",
                "rate",
                "UnitRate",
                "unitRate",
                "Price",
                "price",
                "Amount",
                "amount",
            ]),
            ("Quantity", [
                "Quantity",
                "quantity",
                "Qty",
                "qty",
            ]),
            ("Status", [
                "Status",
                "status",
                "ServiceStatus",
                "serviceStatus",
            ]),
            ("Last Billed Through", [
                "LastBilledThrough",
                "lastBilledThrough",
                "LastBilledThroughDate",
                "lastBilledThroughDate",
                "BilledThrough",
                "billedThrough",
            ]),
            ("Service ID", [
                "ServiceId",
                "serviceId",
                "service_id",
            ]),
            ("Product ID", [
                "ProductId",
                "productId",
                "product_id",
            ]),
        ],
        "Products": [
            ("Product", ["Name", "name", "Description", "description"]),
            ("Product ID", ["ProductId", "productId", "id"]),
            ("Status", ["Status", "status", "Active", "active"]),
            ("Type", ["Type", "type", "ProductType", "productType"]),
        ],
        "Addresses": [
            ("Address", ["Address1", "address1", "Street1", "street1"]),
            ("Address 2", ["Address2", "address2", "Street2", "street2"]),
            ("City", ["City", "city"]),
            ("State", ["StateOrProvince", "stateOrProvince", "State", "state"]),
            ("Postal Code", ["PostalCode", "postalCode", "Zip", "zip"]),
            ("Type", ["AddressType", "addressType", "Type", "type"]),
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

    display_title = title
    if title == "Service Products" and data.get("customer_name"):
        display_title = f"{data['customer_name']} Billing Summary"
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
        title=f"Bullfrog Intelligence - {display_title}",
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
    bullet_style = ParagraphStyle(
        "BullfrogBullet",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        leftIndent=4,
        firstLineIndent=0,
        textColor=colors.HexColor("#26382D"),
        spaceAfter=2,
    )
    small_style = ParagraphStyle(
        "BullfrogSmall",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7.4,
        leading=9,
        textColor=colors.HexColor("#26382D"),
    )

    summary_intro, summary_items = _summary_bullets(answer)

    story: list[Any] = [
        Paragraph("Bullfrog Intelligence", title_style),
        Paragraph(_safe(display_title), heading_style),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%B %d, %Y at %I:%M %p UTC')}",
            body_style,
        ),
        Spacer(1, 10),
        Paragraph("Summary", heading_style),
    ]

    if summary_intro:
        story.extend(
            [
                Paragraph(_safe(summary_intro), body_style),
                Spacer(1, 5),
            ]
        )

    if summary_items:
        story.append(
            ListFlowable(
                [
                    ListItem(
                        Paragraph(_safe(item), bullet_style),
                        leftIndent=12,
                    )
                    for item in summary_items
                ],
                bulletType="bullet",
                start="circle",
                leftIndent=16,
                bulletFontName="Helvetica",
                bulletFontSize=7,
                bulletColor=colors.HexColor("#168345"),
                spaceBefore=2,
                spaceAfter=8,
            )
        )
    elif answer:
        story.append(Paragraph(_safe(answer), body_style))

    story.append(Spacer(1, 8))

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
            cells: list[Any] = []

            for label, keys in columns:
                if title == "Service Products":
                    kind = None
                    if label == "Rate":
                        kind = "currency"
                    elif label == "Quantity":
                        kind = "quantity"

                    value = _service_product_value(
                        row,
                        keys,
                        kind=kind,
                    )
                else:
                    value = _pick(row, keys)

                cells.append(Paragraph(_safe(value), small_style))

            table_data.append(cells)

        available_width = page_size[0] - 0.9 * inch

        if title == "Service Products" and len(columns) == 7:
            column_widths = [
                available_width * 0.30,
                available_width * 0.10,
                available_width * 0.08,
                available_width * 0.10,
                available_width * 0.17,
                available_width * 0.12,
                available_width * 0.13,
            ]
        else:
            col_width = available_width / max(len(columns), 1)
            column_widths = [col_width] * len(columns)

        table = Table(
            table_data,
            colWidths=column_widths,
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
                Paragraph(f"{display_title} Details ({len(rows)} records)", heading_style),
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

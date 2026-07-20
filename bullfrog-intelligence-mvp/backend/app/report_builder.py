from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import csv
import json
import re
import uuid

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .pdf_reports import REPORT_DIR


GREEN = "168345"
DARK_GREEN = colors.HexColor("#0E5D32")
LIGHT_GREEN = colors.HexColor("#EAF5EE")


TEMPLATE_DESCRIPTIONS = {
    "executive": (
        "High-level KPIs, totals, notable findings, and a limited supporting "
        "sample for leadership review."
    ),
    "detailed": (
        "Complete operational records with useful IDs, dates, statuses, "
        "amounts, and source fields."
    ),
    "customer_facing": (
        "Clean customer-ready presentation with internal IDs, API names, "
        "queries, and technical metadata removed."
    ),
    "audit": (
        "Full traceability including dataset IDs, source, original query, "
        "intent, timestamps, complete records, and raw payload evidence."
    ),
}


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return cleaned[:80] or "ribbit-report"


def _friendly_label(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = value.replace("_", " ")
    replacements = {
        "id": "ID",
        "api": "API",
        "url": "URL",
        "psa": "PSA",
    }
    words = []
    for word in value.split():
        words.append(replacements.get(word.casefold(), word.capitalize()))
    return " ".join(words)


def _is_internal_field(key: str) -> bool:
    lowered = key.casefold()
    return (
        lowered == "id"
        or lowered.endswith("_id")
        or lowered.endswith("id")
        or lowered
        in {
            "source",
            "intent",
            "conversation_id",
            "dataset_id",
            "dataset_type",
            "dataset_title",
            "raw",
            "soap_fallback_error",
            "ledger_source",
        }
    )


def _flatten_record(
    record: dict[str, Any],
    *,
    template: str,
) -> dict[str, Any]:
    flat: dict[str, Any] = {}

    for key, value in record.items():
        if key == "raw" and template != "audit":
            continue

        if template == "customer_facing" and _is_internal_field(key):
            continue

        if template == "executive" and (
            _is_internal_field(key)
            or key.casefold()
            in {
                "created_at",
                "modified_at",
                "start_date",
                "end_date",
                "raw",
            }
        ):
            continue

        output_key = (
            _friendly_label(key)
            if template == "customer_facing"
            else key
        )

        if isinstance(value, (dict, list)):
            flat[output_key] = json.dumps(
                value,
                ensure_ascii=False,
                default=str,
            )
        else:
            flat[output_key] = value

    return flat


def _dataset_records(
    dataset: dict[str, Any],
) -> list[dict[str, Any]]:
    data = dataset.get("data") or {}
    data_type = dataset.get("data_type")
    keys = {
        "billing_ledger": "ledger_entries",
        "service_products": "service_products",
        "tickets": "tickets",
        "projects": "projects",
        "invoices": "invoices",
        "opportunities": "opportunities",
        "contacts": "contacts",
        "customers": "customers",
        "project_activity": "activity",
        "customer": "customer",
    }
    value = data.get(keys.get(data_type, ""))

    if isinstance(value, list):
        return [
            item for item in value
            if isinstance(item, dict)
        ]
    if isinstance(value, dict):
        return [value]
    return []


def _number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(
            str(value)
            .replace("$", "")
            .replace(",", "")
        )
    except (TypeError, ValueError):
        return 0.0


def _first(record: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _dataset_insights(
    dataset: dict[str, Any],
) -> list[tuple[str, str]]:
    data = dataset.get("data") or {}
    data_type = dataset.get("data_type")
    records = _dataset_records(dataset)
    insights: list[tuple[str, str]] = [
        ("Records", f"{len(records):,}"),
    ]

    if data_type == "billing_ledger":
        summary = data.get("ledger_summary") or {}
        insights.extend(
            [
                (
                    "Total charges",
                    f"${_number(summary.get('total_charges')):,.2f}",
                ),
                (
                    "Total credits",
                    f"${_number(summary.get('total_credits')):,.2f}",
                ),
                (
                    "Charges less credits",
                    f"${_number(summary.get('net_charges_less_credits')):,.2f}",
                ),
            ]
        )
    elif data_type == "tickets":
        statuses = Counter(
            str(_first(row, ["status", "Status"]) or "Unknown")
            for row in records
        )
        oldest = max(
            (
                _number(
                    _first(
                        row,
                        ["age_days", "ageDays", "AgeDays"],
                    )
                )
                for row in records
            ),
            default=0,
        )
        insights.extend(
            [
                ("Statuses", f"{len(statuses):,}"),
                ("Oldest ticket", f"{oldest:,.0f} days"),
                (
                    "Most common status",
                    statuses.most_common(1)[0][0]
                    if statuses
                    else "Not available",
                ),
            ]
        )
    elif data_type == "invoices":
        total = sum(
            _number(
                _first(
                    row,
                    ["amount", "Amount", "total", "Total"],
                )
            )
            for row in records
        )
        balance = sum(
            _number(
                _first(
                    row,
                    ["balance", "Balance", "amountDue"],
                )
            )
            for row in records
        )
        insights.extend(
            [
                ("Invoice total", f"${total:,.2f}"),
                ("Open balance", f"${balance:,.2f}"),
            ]
        )
    elif data_type == "opportunities":
        total = sum(
            _number(_first(row, ["amount", "Amount"]))
            for row in records
        )
        insights.append(
            ("Pipeline value", f"${total:,.2f}")
        )
    elif data_type == "projects":
        statuses = Counter(
            str(
                _first(
                    row,
                    [
                        "projectStatusName",
                        "statusName",
                        "status",
                    ],
                )
                or "Unknown"
            )
            for row in records
        )
        insights.extend(
            [
                ("Project statuses", f"{len(statuses):,}"),
                (
                    "Most common status",
                    statuses.most_common(1)[0][0]
                    if statuses
                    else "Not available",
                ),
            ]
        )
    elif data_type == "service_products":
        active = sum(
            1
            for row in records
            if str(
                _first(row, ["status", "Status"]) or ""
            ).casefold()
            == "active"
        )
        insights.append(("Active services", f"{active:,}"))

    return insights


def _summary_lines(
    datasets: list[dict[str, Any]],
    *,
    template: str,
) -> list[str]:
    total_records = sum(
        int(item.get("record_count") or 0)
        for item in datasets
    )
    lines = [
        f"Datasets included: {len(datasets)}",
        f"Total records included: {total_records:,}",
    ]

    for dataset in datasets:
        insights = ", ".join(
            f"{label}: {value}"
            for label, value in _dataset_insights(dataset)
        )
        lines.append(
            f"{dataset.get('title', 'Dataset')} - {insights}"
        )

    if template == "audit":
        lines.append(
            "Audit mode includes source metadata, original query, intent, "
            "dataset identifiers, and raw evidence."
        )
    elif template == "customer_facing":
        lines.append(
            "Customer-facing mode excludes internal identifiers and "
            "technical source metadata."
        )
    elif template == "executive":
        lines.append(
            "Executive mode emphasizes decisions and KPIs rather than "
            "record-level operational detail."
        )

    return lines


def _record_limit(
    template: str,
    *,
    include_raw_records: bool,
) -> int | None:
    if template == "executive":
        return 10
    if template == "customer_facing":
        return 100 if include_raw_records else 25
    if template == "detailed":
        return None if include_raw_records else 100
    return None


def create_report(
    *,
    datasets: list[dict[str, Any]],
    title: str,
    report_format: str,
    template: str,
    include_summary: bool,
    include_raw_records: bool,
) -> dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_id = str(uuid.uuid4())
    stem = f"{_safe_filename(title)}-{report_id[:8]}"

    if report_format == "xlsx":
        path = REPORT_DIR / f"{stem}.xlsx"
        _create_xlsx(
            path,
            datasets=datasets,
            title=title,
            template=template,
            include_summary=include_summary,
            include_raw_records=include_raw_records,
        )
    elif report_format == "csv":
        path = REPORT_DIR / f"{stem}.csv"
        _create_csv(
            path,
            datasets=datasets,
            template=template,
            include_raw_records=include_raw_records,
        )
    else:
        path = REPORT_DIR / f"{stem}.pdf"
        _create_pdf(
            path,
            datasets=datasets,
            title=title,
            template=template,
            include_summary=include_summary,
            include_raw_records=include_raw_records,
        )

    return {
        "report_id": report_id,
        "download_url": f"/api/downloads/{path.name}",
        "download_name": path.name,
        "format": report_format,
    }


def _style_summary_sheet(
    ws,
    *,
    title: str,
    template: str,
    datasets: list[dict[str, Any]],
) -> None:
    ws["A1"] = title
    ws["A1"].font = Font(
        size=20,
        bold=True,
        color=GREEN,
    )
    ws["A2"] = (
        f"Generated "
        f"{datetime.now(timezone.utc).strftime('%B %d, %Y %I:%M %p UTC')}"
    )
    ws["A4"] = "Report type"
    ws["B4"] = template.replace("_", " ").title()
    ws["A5"] = "Purpose"
    ws["B5"] = TEMPLATE_DESCRIPTIONS[template]
    ws["B5"].alignment = Alignment(wrap_text=True)
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 95

    row = 7
    for line in _summary_lines(
        datasets,
        template=template,
    ):
        ws.cell(row=row, column=1, value="•")
        ws.cell(row=row, column=2, value=line)
        ws.cell(row=row, column=2).alignment = Alignment(
            wrap_text=True
        )
        row += 1


def _create_xlsx(
    path: Path,
    *,
    datasets: list[dict[str, Any]],
    title: str,
    template: str,
    include_summary: bool,
    include_raw_records: bool,
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Executive Summary" if template == "executive" else "Summary"
    _style_summary_sheet(
        ws,
        title=title,
        template=template,
        datasets=datasets,
    )

    if template == "audit":
        audit = wb.create_sheet("Audit Trail")
        headers = [
            "Dataset ID",
            "Conversation ID",
            "Title",
            "Data Type",
            "Source",
            "Original Query",
            "Intent",
            "Created At",
            "Record Count",
        ]
        for col, header in enumerate(headers, start=1):
            cell = audit.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=GREEN)

        for row_index, dataset in enumerate(datasets, start=2):
            values = [
                dataset.get("dataset_id"),
                dataset.get("conversation_id"),
                dataset.get("title"),
                dataset.get("data_type"),
                dataset.get("source"),
                dataset.get("query"),
                dataset.get("intent"),
                dataset.get("created_at"),
                dataset.get("record_count"),
            ]
            for col, value in enumerate(values, start=1):
                audit.cell(row=row_index, column=col, value=value)

        for col in range(1, len(headers) + 1):
            audit.column_dimensions[get_column_letter(col)].width = 24
        audit.column_dimensions["F"].width = 70
        audit.freeze_panes = "A2"

    for index, dataset in enumerate(datasets, start=1):
        records = _dataset_records(dataset)
        if not records:
            continue

        sheet_name = re.sub(
            r"[:\\/?*\[\]]",
            "",
            str(dataset.get("title") or f"Dataset {index}"),
        )
        sheet_name = sheet_name[:31] or f"Dataset {index}"
        if sheet_name in wb.sheetnames:
            sheet_name = f"{sheet_name[:27]} {index}"

        sheet = wb.create_sheet(sheet_name)
        limit = _record_limit(
            template,
            include_raw_records=include_raw_records,
        )
        selected_records = (
            records
            if limit is None
            else records[:limit]
        )
        rows = [
            _flatten_record(
                record,
                template=template,
            )
            for record in selected_records
        ]
        headers = list(
            dict.fromkeys(
                key
                for record in rows
                for key in record.keys()
            )
        )

        if not headers:
            sheet["A1"] = "No customer-safe fields were available."
            continue

        for col, header in enumerate(headers, start=1):
            cell = sheet.cell(
                row=1,
                column=col,
                value=header,
            )
            cell.font = Font(
                bold=True,
                color="FFFFFF",
            )
            cell.fill = PatternFill(
                "solid",
                fgColor=GREEN,
            )
            cell.alignment = Alignment(
                wrap_text=True,
            )

        for row_index, record in enumerate(rows, start=2):
            for col, header in enumerate(headers, start=1):
                sheet.cell(
                    row=row_index,
                    column=col,
                    value=record.get(header),
                )

        for col, header in enumerate(headers, start=1):
            width = max(
                len(str(header)),
                max(
                    (
                        len(str(record.get(header, "")))
                        for record in rows[:200]
                    ),
                    default=0,
                ),
            )
            sheet.column_dimensions[
                get_column_letter(col)
            ].width = min(
                max(width + 2, 12),
                45,
            )

        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

        if template == "audit":
            raw_sheet_name = f"Raw {index}"[:31]
            raw_sheet = wb.create_sheet(raw_sheet_name)
            raw_sheet["A1"] = "Dataset ID"
            raw_sheet["B1"] = dataset.get("dataset_id")
            raw_sheet["A2"] = "Raw JSON"
            raw_sheet["B2"] = json.dumps(
                dataset.get("data") or {},
                ensure_ascii=False,
                default=str,
            )
            raw_sheet["B2"].alignment = Alignment(
                wrap_text=True,
                vertical="top",
            )
            raw_sheet.column_dimensions["A"].width = 18
            raw_sheet.column_dimensions["B"].width = 120

    wb.save(path)


def _create_csv(
    path: Path,
    *,
    datasets: list[dict[str, Any]],
    template: str,
    include_raw_records: bool,
) -> None:
    rows: list[dict[str, Any]] = []
    limit = _record_limit(
        template,
        include_raw_records=include_raw_records,
    )

    for dataset in datasets:
        records = _dataset_records(dataset)
        selected_records = (
            records
            if limit is None
            else records[:limit]
        )

        for record in selected_records:
            flat = _flatten_record(
                record,
                template=template,
            )

            if template == "audit":
                flat = {
                    "dataset_id": dataset.get("dataset_id"),
                    "conversation_id": dataset.get("conversation_id"),
                    "dataset_title": dataset.get("title"),
                    "data_type": dataset.get("data_type"),
                    "source": dataset.get("source"),
                    "original_query": dataset.get("query"),
                    "intent": dataset.get("intent"),
                    "dataset_created_at": dataset.get("created_at"),
                    **flat,
                    "raw_dataset_json": json.dumps(
                        dataset.get("data") or {},
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            elif template != "customer_facing":
                flat = {
                    "dataset_title": dataset.get("title"),
                    "data_type": dataset.get("data_type"),
                    **flat,
                }

            rows.append(flat)

    headers = list(
        dict.fromkeys(
            key
            for row in rows
            for key in row.keys()
        )
    )
    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=headers,
        )
        writer.writeheader()
        writer.writerows(rows)


def _pdf_styles():
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "RibbitTitle",
            parent=styles["Title"],
            textColor=DARK_GREEN,
            fontSize=22,
            leading=26,
            alignment=TA_CENTER,
        ),
        "heading": ParagraphStyle(
            "RibbitHeading",
            parent=styles["Heading2"],
            textColor=DARK_GREEN,
            fontSize=14,
            leading=18,
        ),
        "body": ParagraphStyle(
            "RibbitBody",
            parent=styles["BodyText"],
            fontSize=9,
            leading=13,
        ),
        "small": ParagraphStyle(
            "RibbitSmall",
            parent=styles["BodyText"],
            fontSize=7,
            leading=9,
        ),
    }


def _pdf_kpi_table(
    dataset: dict[str, Any],
    *,
    body_style,
) -> Table:
    insights = _dataset_insights(dataset)
    data = [
        [
            Paragraph(label, body_style),
            Paragraph(value, body_style),
        ]
        for label, value in insights
    ]
    table = Table(
        data,
        colWidths=[2.2 * inch, 2.2 * inch],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_GREEN),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#17351F")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBD2C2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _create_pdf(
    path: Path,
    *,
    datasets: list[dict[str, Any]],
    title: str,
    template: str,
    include_summary: bool,
    include_raw_records: bool,
) -> None:
    styles = _pdf_styles()
    page_size = (
        letter
        if template in {"executive", "customer_facing"}
        else landscape(letter)
    )

    doc = SimpleDocTemplate(
        str(path),
        pagesize=page_size,
        rightMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=title,
    )

    story: list[Any] = [
        Paragraph(title, styles["title"]),
        Paragraph(
            f"Generated "
            f"{datetime.now(timezone.utc).strftime('%B %d, %Y at %I:%M %p UTC')}",
            styles["body"],
        ),
        Spacer(1, 10),
        Paragraph(
            TEMPLATE_DESCRIPTIONS[template],
            styles["body"],
        ),
        Spacer(1, 12),
    ]

    if include_summary:
        story.append(
            Paragraph(
                "Executive Overview"
                if template == "executive"
                else "Report Summary",
                styles["heading"],
            )
        )
        for line in _summary_lines(
            datasets,
            template=template,
        ):
            story.append(
                Paragraph(
                    f"• {line}",
                    styles["body"],
                )
            )
        story.append(Spacer(1, 10))

    for dataset_index, dataset in enumerate(datasets):
        if dataset_index:
            story.append(PageBreak())

        dataset_title = str(
            dataset.get("title") or "Dataset"
        )
        story.append(
            Paragraph(
                dataset_title,
                styles["heading"],
            )
        )

        if template == "audit":
            metadata = [
                ["Dataset ID", dataset.get("dataset_id")],
                ["Conversation ID", dataset.get("conversation_id")],
                ["Data type", dataset.get("data_type")],
                ["Source", dataset.get("source")],
                ["Original query", dataset.get("query")],
                ["Intent", dataset.get("intent")],
                ["Created at", dataset.get("created_at")],
                ["Record count", dataset.get("record_count")],
            ]
            audit_table = Table(
                [
                    [
                        Paragraph(str(label), styles["small"]),
                        Paragraph(str(value or ""), styles["small"]),
                    ]
                    for label, value in metadata
                ],
                colWidths=[1.3 * inch, 8.6 * inch],
            )
            audit_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, -1), LIGHT_GREEN),
                        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#BBD2C2")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(audit_table)
            story.append(Spacer(1, 10))
        elif template != "customer_facing":
            story.append(
                Paragraph(
                    f"Type: {dataset.get('data_type')} | "
                    f"Source: {dataset.get('source')} | "
                    f"Records: {dataset.get('record_count', 0)}",
                    styles["body"],
                )
            )
            story.append(Spacer(1, 8))

        if template == "executive":
            story.append(
                _pdf_kpi_table(
                    dataset,
                    body_style=styles["body"],
                )
            )
            story.append(Spacer(1, 12))
            story.append(
                Paragraph(
                    "Supporting sample",
                    styles["heading"],
                )
            )

        records = _dataset_records(dataset)
        if not records:
            story.append(
                Paragraph(
                    "No tabular records were available.",
                    styles["body"],
                )
            )
            continue

        limit = _record_limit(
            template,
            include_raw_records=include_raw_records,
        )
        selected_records = (
            records
            if limit is None
            else records[:limit]
        )
        rows = [
            _flatten_record(
                record,
                template=template,
            )
            for record in selected_records
        ]
        headers = list(
            dict.fromkeys(
                key
                for record in rows
                for key in record.keys()
            )
        )

        if template == "executive":
            headers = headers[:6]
        elif template == "customer_facing":
            headers = headers[:8]
        elif template == "detailed":
            headers = headers[:12]
        else:
            headers = headers[:14]

        if not headers:
            story.append(
                Paragraph(
                    "No customer-safe fields were available.",
                    styles["body"],
                )
            )
            continue

        table_data = [
            [
                Paragraph(
                    _friendly_label(str(header)),
                    styles["small"],
                )
                for header in headers
            ]
        ]
        for record in rows:
            table_data.append(
                [
                    Paragraph(
                        str(record.get(header, ""))[:700],
                        styles["small"],
                    )
                    for header in headers
                ]
            )

        available_width = (
            page_size[0]
            - doc.leftMargin
            - doc.rightMargin
        )
        col_width = available_width / max(
            len(headers),
            1,
        )
        table = Table(
            table_data,
            colWidths=[col_width] * len(headers),
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), DARK_GREEN),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C9D7CE")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F8F4")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)

        if template == "audit":
            story.append(Spacer(1, 12))
            story.append(
                Paragraph(
                    "Raw Dataset Evidence",
                    styles["heading"],
                )
            )
            raw_text = json.dumps(
                dataset.get("data") or {},
                ensure_ascii=False,
                default=str,
                indent=2,
            )
            # Keep PDF stable; full raw JSON remains in audit Excel/CSV.
            story.append(
                Paragraph(
                    raw_text[:20_000]
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace("\n", "<br/>"),
                    styles["small"],
                )
            )
            if len(raw_text) > 20_000:
                story.append(
                    Paragraph(
                        "Raw JSON was shortened in the PDF for readability. "
                        "Use the Audit Excel or CSV export for the full payload.",
                        styles["small"],
                    )
                )

    doc.build(story)

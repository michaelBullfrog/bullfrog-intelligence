from __future__ import annotations

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


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return cleaned[:80] or "ribbit-report"


def _flatten_record(
    record: dict[str, Any],
    *,
    include_internal_ids: bool,
) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in record.items():
        lowered = key.casefold()
        if key == "raw":
            continue
        if not include_internal_ids and (
            lowered == "id"
            or lowered.endswith("_id")
            or lowered.endswith("id")
            or lowered in {"source", "intent", "conversation_id"}
        ):
            continue
        if isinstance(value, (dict, list)):
            flat[key] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            flat[key] = value
    return flat


def _dataset_records(dataset: dict[str, Any]) -> list[dict[str, Any]]:
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
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _summary_lines(datasets: list[dict[str, Any]]) -> list[str]:
    total_records = sum(int(item.get("record_count") or 0) for item in datasets)
    lines = [
        f"Datasets included: {len(datasets)}",
        f"Total records included: {total_records}",
    ]
    for dataset in datasets:
        lines.append(
            f"{dataset.get('title', 'Dataset')}: "
            f"{dataset.get('record_count', 0)} records"
        )
    return lines


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
    ws.title = "Summary"

    ws["A1"] = title
    ws["A1"].font = Font(size=18, bold=True, color=GREEN)
    ws["A2"] = f"Generated {datetime.now(timezone.utc).strftime('%B %d, %Y %I:%M %p UTC')}"
    ws["A4"] = "Template"
    ws["B4"] = template.replace("_", " ").title()

    row = 6
    if include_summary:
        for line in _summary_lines(datasets):
            ws.cell(row=row, column=1, value=line)
            row += 1

    for index, dataset in enumerate(datasets, start=1):
        sheet_name = re.sub(r"[:\\/?*\[\]]", "", str(dataset.get("title") or f"Dataset {index}"))
        sheet_name = sheet_name[:31] or f"Dataset {index}"
        if sheet_name in wb.sheetnames:
            sheet_name = f"{sheet_name[:27]} {index}"

        sheet = wb.create_sheet(sheet_name)
        records = _dataset_records(dataset)
        if not records:
            sheet["A1"] = "No tabular records were available."
            continue

        rows = [
            _flatten_record(
                record,
                include_internal_ids=(template != "customer_facing"),
            )
            for record in records
        ]
        headers = list(dict.fromkeys(key for record in rows for key in record.keys()))

        for col, header in enumerate(headers, start=1):
            cell = sheet.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=GREEN)
            cell.alignment = Alignment(wrap_text=True)

        for row_index, record in enumerate(rows, start=2):
            for col, header in enumerate(headers, start=1):
                value = record.get(header)
                sheet.cell(row=row_index, column=col, value=value)

        for col, header in enumerate(headers, start=1):
            width = max(
                len(str(header)),
                max(
                    (len(str(record.get(header, ""))) for record in rows[:200]),
                    default=0,
                ),
            )
            sheet.column_dimensions[get_column_letter(col)].width = min(max(width + 2, 12), 45)

        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

    wb.save(path)


def _create_csv(
    path: Path,
    *,
    datasets: list[dict[str, Any]],
    include_raw_records: bool,
) -> None:
    rows: list[dict[str, Any]] = []
    for dataset in datasets:
        for record in _dataset_records(dataset):
            flat = _flatten_record(record, include_internal_ids=True)
            flat = {
                "dataset_title": dataset.get("title"),
                "data_type": dataset.get("data_type"),
                **flat,
            }
            rows.append(flat)

    headers = list(dict.fromkeys(key for row in rows for key in row.keys()))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _create_pdf(
    path: Path,
    *,
    datasets: list[dict[str, Any]],
    title: str,
    template: str,
    include_summary: bool,
    include_raw_records: bool,
) -> None:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "RibbitTitle",
        parent=styles["Title"],
        textColor=DARK_GREEN,
        fontSize=22,
        leading=26,
        alignment=TA_CENTER,
    )
    heading = ParagraphStyle(
        "RibbitHeading",
        parent=styles["Heading2"],
        textColor=DARK_GREEN,
        fontSize=14,
        leading=18,
    )
    body = ParagraphStyle(
        "RibbitBody",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13,
    )

    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(letter),
        rightMargin=0.4 * inch,
        leftMargin=0.4 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        title=title,
    )
    story: list[Any] = [
        Paragraph(title, title_style),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%B %d, %Y at %I:%M %p UTC')}",
            body,
        ),
        Spacer(1, 12),
    ]

    if include_summary:
        story.append(Paragraph("Report Summary", heading))
        for line in _summary_lines(datasets):
            story.append(Paragraph(f"• {line}", body))
        story.append(Spacer(1, 10))

    for dataset_index, dataset in enumerate(datasets):
        if dataset_index:
            story.append(PageBreak())

        story.append(Paragraph(str(dataset.get("title") or "Dataset"), heading))
        story.append(
            Paragraph(
                f"Type: {dataset.get('data_type')} | "
                f"Source: {dataset.get('source')} | "
                f"Records: {dataset.get('record_count', 0)}",
                body,
            )
        )
        story.append(Spacer(1, 8))

        records = _dataset_records(dataset)
        if not records:
            story.append(Paragraph("No tabular records were available.", body))
            continue

        rows = [
            _flatten_record(
                record,
                include_internal_ids=(template != "customer_facing"),
            )
            for record in records
        ]

        headers = list(dict.fromkeys(key for record in rows for key in record.keys()))
        # Keep PDF readable. Excel/CSV retain wider datasets.
        headers = headers[:10]
        table_data = [
            [Paragraph(str(header), body) for header in headers]
        ]
        limit = len(rows) if include_raw_records else min(len(rows), 50)
        for record in rows[:limit]:
            table_data.append(
                [
                    Paragraph(str(record.get(header, ""))[:500], body)
                    for header in headers
                ]
            )

        available_width = 10.2 * inch
        col_width = available_width / max(len(headers), 1)
        table = Table(table_data, colWidths=[col_width] * len(headers), repeatRows=1)
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

    doc.build(story)

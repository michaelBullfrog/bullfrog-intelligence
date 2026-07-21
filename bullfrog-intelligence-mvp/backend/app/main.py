from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .models import ChatRequest, ChatResponse, ReportRequest, BuildReportRequest, CcwrNormalizedRequest, CcwrIngestRequest
from .orchestrator import handle_chat, revio, webex, ccwr, documents
from .pdf_reports import REPORT_DIR
from .security import require_permission
from .dataset_store import (
    get_selected_datasets,
    list_conversation_dataset_summaries,
    list_conversation_reports,
    save_report,
)
from .report_builder import create_report as build_report_file
from .database import initialize_database, database_health
from .config import settings
from .renewal_store import get_latest_sync, get_renewal_snapshot, replace_renewal_snapshot

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Ribbit API",
    version="0.3.0",
    description="Internal AI operations and reporting API for Bullfrog Group.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
    ],
    allow_origin_regex=r"https://.*\.onrender\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REPORT_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/api/downloads",
    StaticFiles(directory=str(REPORT_DIR)),
    name="downloads",
)


@app.get("/")
async def root():
    return {
        "name": "Bullfrog Intelligence API",
        "status": "online",
        "version": "0.2.0",
    }


@app.get("/api/health")
async def health():
    return {
        "status": "online",
        "connectors": [
            await revio.health(),
            await webex.health(),
            await ccwr.health(),
            await documents.health(),
        ],
    }



@app.get("/api/integrations/ccwr/health")
async def ccwr_health_endpoint():
    return await ccwr.health()


@app.get("/api/integrations/ccwr/test")
async def ccwr_test_endpoint(
    market: str = "US",
    days: int = 30,
    page_size: int = 10,
):
    """
    Run a deliberately small live CCW-R subscription search.

    This endpoint is for integration validation. It limits the date range
    to 180 days and page size to 25 records.
    """
    try:
        result = await ccwr.test_search(
            market=market,
            days=days,
            page_size=page_size,
        )
        return {
            "status": "online",
            **result,
        }
    except Exception as exc:
        return {
            "status": "error",
            "market": market,
            "error": str(exc),
        }




@app.post("/api/ingest/ccwr-renewals")
async def ingest_ccwr_renewals(
    request: CcwrIngestRequest,
    authorization: str | None = Header(default=None),
):
    expected = settings.ccwr_ingest_api_key.strip()
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or supplied != expected:
        raise HTTPException(status_code=401, detail="Invalid ingestion API key")
    if not request.replace_snapshot:
        raise HTTPException(status_code=400, detail="Only full snapshot replacement is supported")
    return replace_renewal_snapshot(
        sync_id=request.sync_id,
        refreshed_at=request.refreshed_at,
        records=[record.model_dump() for record in request.records],
    )


@app.get("/api/integrations/ccwr/database-summary")
async def ccwr_database_summary():
    snapshot = get_renewal_snapshot()
    return {
        "status": "online",
        **snapshot["renewal_summary"],
        "latest_sync": get_latest_sync(),
    }


@app.post("/api/integrations/ccwr/normalized-search")
async def ccwr_normalized_search(
    request: CcwrNormalizedRequest,
):
    result = await ccwr.search_recent_normalized(
        market=request.market,
        days=request.days,
        page_size=request.page_size,
    )

    records = result.get("ccwr_renewals") or []

    if request.customer_name:
        needle = request.customer_name.casefold().strip()
        records = [
            record
            for record in records
            if needle
            in str(
                record.get("end_customer_name") or ""
            ).casefold()
        ]

    if request.renewal_scope == "past_due":
        records = [
            record
            for record in records
            if record.get("is_past_due") is True
        ]
    elif request.renewal_scope == "next_30":
        records = [
            record
            for record in records
            if isinstance(
                record.get("days_until_renewal"),
                int,
            )
            and 0 <= record["days_until_renewal"] <= 30
        ]
    elif request.renewal_scope == "next_60":
        records = [
            record
            for record in records
            if isinstance(
                record.get("days_until_renewal"),
                int,
            )
            and 0 <= record["days_until_renewal"] <= 60
        ]
    elif request.renewal_scope == "next_90":
        records = [
            record
            for record in records
            if isinstance(
                record.get("days_until_renewal"),
                int,
            )
            and 0 <= record["days_until_renewal"] <= 90
        ]

    from .connectors.ccwr import summarize_renewals
    from .dataset_store import save_dataset

    filtered_result = {
        **result,
        "ccwr_renewals": records,
        "renewal_summary": summarize_renewals(records),
        "customer_name": request.customer_name,
        "renewal_scope": request.renewal_scope,
    }

    dataset = save_dataset(
        conversation_id=request.conversation_id,
        query=(
            f"CCW-R normalized search: market={request.market}, "
            f"days={request.days}, "
            f"customer={request.customer_name}, "
            f"scope={request.renewal_scope}"
        ),
        intent="ccwr_renewal_search",
        data=filtered_result,
        title=(
            f"{request.customer_name} Cisco Renewals"
            if request.customer_name
            else f"{request.market} Cisco Renewals"
        ),
    )

    return {
        "status": "online",
        "dataset": (
            {
                key: value
                for key, value in dataset.items()
                if key != "data"
            }
            if dataset
            else None
        ),
        **filtered_result,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    return await handle_chat(request)


@app.post("/api/reports")
async def create_report(request: ReportRequest):
    require_permission(request.user, "reports")
    return {
        "status": "accepted",
        "report_type": request.report_type,
        "message": (
            "Use the chat interface to retrieve live data and then ask "
            "Bullfrog Intelligence to create a PDF."
        ),
        "parameters": request.model_dump(exclude={"user"}),
    }



@app.get("/api/conversations/{conversation_id}/datasets")
async def conversation_datasets(conversation_id: str):
    datasets = list_conversation_dataset_summaries(conversation_id)
    return {
        "conversation_id": conversation_id,
        "datasets": datasets,
    }


@app.post("/api/reports/build")
async def build_report(request: BuildReportRequest):
    require_permission(request.user, "reports")

    datasets = get_selected_datasets(
        conversation_id=request.conversation_id,
        dataset_ids=request.dataset_ids,
        scope=request.scope,
    )
    if not datasets:
        return {
            "status": "error",
            "message": (
                "No saved datasets were found for this report. "
                "Run a data search first or select a dataset."
            ),
        }

    result = build_report_file(
        datasets=datasets,
        title=request.title,
        report_format=request.format,
        template=request.template,
        include_summary=request.include_summary,
        include_raw_records=request.include_raw_records,
    )

    report_record = save_report(
        conversation_id=request.conversation_id,
        title=request.title,
        report_format=request.format,
        template=request.template,
        dataset_ids=[
            str(dataset["dataset_id"])
            for dataset in datasets
        ],
        download_name=result["download_name"],
        download_url=result["download_url"],
        report_id=result["report_id"],
    )

    return {
        "status": "ready",
        "datasets_included": len(datasets),
        "report": report_record,
        **result,
    }



@app.get("/api/database/health")
async def database_health_endpoint():
    try:
        return {
            "status": "online",
            **database_health(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "connected": False,
            "error": str(exc),
        }


@app.get("/api/conversations/{conversation_id}/reports")
async def conversation_reports(conversation_id: str):
    return {
        "conversation_id": conversation_id,
        "reports": list_conversation_reports(
            conversation_id
        ),
    }

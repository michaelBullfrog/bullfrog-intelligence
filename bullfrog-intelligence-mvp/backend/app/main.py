from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .models import ChatRequest, ChatResponse, ReportRequest
from .orchestrator import handle_chat, revio, webex, ccwr, documents
from .pdf_reports import REPORT_DIR
from .security import require_permission

app = FastAPI(
    title="Bullfrog Intelligence API",
    version="0.2.0",
    description="Internal AI operations and reporting API for Bullfrog Group.",
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

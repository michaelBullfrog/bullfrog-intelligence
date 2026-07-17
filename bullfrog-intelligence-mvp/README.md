# Bullfrog Intelligence MVP

Private internal AI portal for Bullfrog Group.

## Initial capabilities

- Web chat interface
- Voice-ready frontend placeholder
- FastAPI orchestration backend
- Connector framework for Rev.io PSA, Webex, CCW-R, and internal documents
- Source-aware responses
- Report request endpoint
- Entra ID integration placeholder
- PostgreSQL-ready configuration

## Architecture

Frontend:
- Next.js
- TypeScript
- Entra ID authentication can be added with MSAL

Backend:
- Python
- FastAPI
- OpenAI Responses API integration placeholder
- Platform connector layer
- Permission enforcement layer
- PostgreSQL / Azure SQL compatible data layer

## Run the backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

## Run the frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Open http://localhost:3000.

## First implementation order

1. Entra ID authentication
2. Rev.io PSA read-only connector
3. Internal document search
4. Customer identity mapping
5. Webex Contact Center reporting connector
6. CCW-R renewals connector
7. Excel/PDF report generation
8. Voice using the OpenAI Realtime API

## Important security rule

The AI model never receives unrestricted database or API credentials. Every request goes through approved backend tools that enforce the signed-in user's permissions.

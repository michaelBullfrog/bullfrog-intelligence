from typing import Any
from .base import Connector

class DocumentConnector(Connector):
    name = "documents"

    async def health(self) -> dict[str, Any]:
        return {"system": self.name, "configured": False}

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        return [{
            "title": "Document search not configured",
            "snippet": f"Connect SharePoint, Google Drive, or an indexed document store for: {query}",
            "source": "demo",
        }][:max_results]

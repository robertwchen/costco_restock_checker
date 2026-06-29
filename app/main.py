"""Application entrypoint.

This module is intentionally minimal at the skeleton stage. Database,
scheduler, alerting, and the dashboard UI are wired in over later milestones.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Costco Restock Checker")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by containers and uptime checks."""
    return {"status": "ok"}


@app.get("/")
def index() -> dict[str, str]:
    return {"service": "costco-restock-checker", "status": "ok"}

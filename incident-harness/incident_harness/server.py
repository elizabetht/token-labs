"""
server.py — FastAPI webhook receiver

Alertmanager POSTs to /alertmanager-webhook when an alert fires.
We run each alert through the harness in a background task so the
HTTP response returns immediately (Alertmanager expects < 10s response).

One endpoint, one purpose.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from .config import Config
from .harness import IncidentHarness

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="incident-harness", version="0.1.0")
_harness: IncidentHarness | None = None


@app.on_event("startup")
async def startup():
    global _harness
    cfg = Config.from_env()
    _harness = IncidentHarness(cfg)
    log.info("Harness ready. LLM=%s model=%s", cfg.llm_url, cfg.llm_model)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/alertmanager-webhook")
async def alertmanager_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Alertmanager webhook receiver.

    Alertmanager sends a JSON payload with a list of `alerts`.
    We fan out one triage task per firing alert.
    Resolved alerts (status=resolved) are acknowledged but not triaged.
    """
    payload = await request.json()

    for alert in payload.get("alerts", []):
        if alert.get("status") == "resolved":
            log.info("Alert resolved: %s — skipping triage", alert.get("labels", {}).get("alertname"))
            continue

        # Wrap each alert in its own payload so harness.run() can parse it
        single_alert_payload = {**payload, "alerts": [alert]}
        background_tasks.add_task(_run_triage, single_alert_payload)

    return JSONResponse({"status": "accepted", "alerts": len(payload.get("alerts", []))})


async def _run_triage(payload: dict):
    try:
        incident = await _harness.run(payload)
        log.info(
            "Triage complete: alert=%s category=%s severity=%s action=%s",
            incident.alert_name,
            incident.classification.category if incident.classification else "?",
            incident.classification.severity if incident.classification else "?",
            incident.action_taken,
        )
    except Exception:
        log.exception("Triage failed for payload: %s", payload.get("alerts", [{}])[0].get("labels"))

"""
pagerduty.py — trigger and resolve PagerDuty incidents via Events API v2

Only called for severity=critical/high alerts after incident-harness has
enriched and classified the incident. PagerDuty receives AI-diagnosed
context, not raw Prometheus alert data.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"


async def trigger_incident(routing_key: str, incident) -> None:
    payload = build_pagerduty_payload(routing_key, incident, event_action="trigger")
    await _send(payload)


async def resolve_incident(routing_key: str, dedup_key: str) -> None:
    payload = {
        "routing_key": routing_key,
        "event_action": "resolve",
        "dedup_key": dedup_key,
    }
    await _send(payload)


async def _send(payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(PAGERDUTY_EVENTS_URL, json=payload)
            resp.raise_for_status()
            log.info("PagerDuty event sent: %s", resp.json().get("status"))
    except Exception as e:
        log.error("PagerDuty notification failed: %s", e)


# TODO(human): implement build_pagerduty_payload below.
#
# This function must return a dict matching PagerDuty Events API v2 schema:
#   https://developer.pagerduty.com/docs/events-api-v2/trigger-events/
#
# Required top-level fields:
#   routing_key    — passed in as argument
#   event_action   — "trigger" or "resolve" (passed in)
#   dedup_key      — stable ID used to: (1) deduplicate duplicate alerts,
#                    (2) link the resolve event back to the trigger.
#
# Required payload fields (nested under "payload" key):
#   summary        — alert title shown in PD (< 1024 chars)
#   severity       — "critical" | "error" | "warning" | "info"
#                    (PD doesn't have "high"; map from incident severity)
#   source         — where the alert originated ("tokenlabs.run")
#   timestamp      — ISO 8601 UTC (use incident.starts_at)
#   custom_details — dict of extra context for the on-call engineer
#
# Design decisions to think through:
#   - dedup_key: "{alertname}/{namespace}" is stable across pod churn.
#     "{alertname}/{pod}" is more specific but breaks on pod restart.
#     What tradeoff do you prefer?
#   - custom_details: you have incident.metrics, incident.classification.summary,
#     incident.classification.recommendation, and incident.logs.
#     Too much = noise; too little = useless. What would YOU want at 3am?
#   - severity mapping: prometheus "high" → PD "error" or "critical"?
#   - Should you include a log snippet in custom_details? If so, how many lines?
#
# The incident object has these attributes:
#   incident.alert_name        str
#   incident.namespace         str
#   incident.pod               str
#   incident.starts_at         str  (ISO timestamp)
#   incident.metrics           dict (keys: gpu_util_pct, gpu_mem_used_gb, etc.)
#   incident.logs              str  (raw log lines)
#   incident.classification    IncidentClassification | None
#     .category                str
#     .severity                str  ("low"|"medium"|"high"|"critical")
#     .summary                 str
#     .recommendation          str
#     .auto_restartable        bool

def build_pagerduty_payload(routing_key: str, incident, event_action: str) -> dict:
    raise NotImplementedError("Implement build_pagerduty_payload(). See the TODO above.")

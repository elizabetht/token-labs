"""
harness.py — the triage state machine

States:
  DETECT   parse the incoming Alertmanager webhook payload
  ENRICH   fetch logs (Loki) + metrics (Prometheus) for the affected pod
  CLASSIFY send enriched context to the LLM, get structured diagnosis
  ACT      take automated action if safe (e.g. restart a crashed pod)
  NOTIFY   post the full triage report to Slack
  DONE

Why a state machine?
  Each state has a single responsibility. If the ENRICH step fails (Loki
  is down), we still proceed to CLASSIFY with whatever we have — the LLM
  handles partial context gracefully. If CLASSIFY fails, we still NOTIFY
  with the raw alert data. No state failure blocks the Slack message.

Why not let the LLM decide what to do next?
  Incident response has strict paths: you always enrich before classifying,
  always classify before acting. A free-form "agent" that picks tools could
  skip enrichment, loop on the wrong tool, or hallucinate an action.
  Deterministic transitions prevent all of that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from .config import Config
from .llm import IncidentClassification, classify
from .slack import post_triage
from .tools import query_loki, query_prometheus, restart_pod

log = logging.getLogger(__name__)


class State(Enum):
    DETECT   = auto()
    ENRICH   = auto()
    CLASSIFY = auto()
    ACT      = auto()
    NOTIFY   = auto()
    DONE     = auto()


@dataclass
class Incident:
    """Mutable bag of state that flows through the pipeline."""
    alert_name: str
    namespace: str
    pod: str
    labels: dict
    starts_at: str

    # Filled during ENRICH
    logs: str = ""
    metrics: dict = field(default_factory=dict)

    # Filled during CLASSIFY
    classification: Optional[IncidentClassification] = None

    # Filled during ACT
    action_taken: str = ""


class IncidentHarness:
    def __init__(self, config: Config):
        self.cfg = config

    # ── Public entry point ─────────────────────────────────────────────────

    async def run(self, alert_payload: dict) -> Incident:
        """
        Drive the state machine from DETECT to DONE.
        Returns the completed Incident for testing/logging.
        """
        incident = self._parse_alert(alert_payload)
        state = State.DETECT

        while state != State.DONE:
            log.info("[%s] state=%s pod=%s", incident.alert_name, state.name, incident.pod)

            if state == State.DETECT:
                state = State.ENRICH

            elif state == State.ENRICH:
                await self._enrich(incident)
                state = State.CLASSIFY

            elif state == State.CLASSIFY:
                await self._classify(incident)
                state = State.ACT

            elif state == State.ACT:
                self._act(incident)
                state = State.NOTIFY

            elif state == State.NOTIFY:
                await self._notify(incident)
                state = State.DONE

        return incident

    # ── State handlers ─────────────────────────────────────────────────────

    def _parse_alert(self, payload: dict) -> Incident:
        """Extract structured fields from an Alertmanager webhook payload."""
        alert = payload.get("alerts", [{}])[0]
        labels = alert.get("labels", {})
        return Incident(
            alert_name=labels.get("alertname", "UnknownAlert"),
            namespace=labels.get("namespace", "token-labs"),
            pod=labels.get("pod", labels.get("instance", "unknown")),
            labels=labels,
            starts_at=alert.get("startsAt", ""),
        )

    async def _enrich(self, incident: Incident) -> None:
        """Fetch logs and metrics in parallel. Failures are non-fatal."""
        import asyncio
        logs_task    = query_loki(self.cfg.loki_url, incident.namespace, incident.pod, limit=self.cfg.log_lines)
        metrics_task = query_prometheus(self.cfg.prometheus_url, incident.pod, incident.namespace)
        incident.logs, incident.metrics = await asyncio.gather(logs_task, metrics_task)

    async def _classify(self, incident: Incident) -> None:
        """Build the triage prompt and call the LLM."""
        prompt = _build_triage_prompt(incident)
        incident.classification = await classify(self.cfg.llm_url, self.cfg.llm_model, prompt)

    def _act(self, incident: Incident) -> None:
        """
        Take automated action based on classification.

        Restarts are only attempted if:
          1. auto_restart is enabled in config (off by default)
          2. The LLM flagged the incident as auto_restartable
          3. The classification is a known-restartable category

        This is the safety gate. The LLM recommends; the harness decides.
        """
        if not self.cfg.auto_restart:
            incident.action_taken = "none (auto_restart disabled)"
            return

        cls = incident.classification
        if cls and cls.auto_restartable and cls.category in ("oom", "model_crash", "cuda_error"):
            incident.action_taken = restart_pod(incident.namespace, incident.pod)
        else:
            incident.action_taken = "none (manual review required)"

    async def _notify(self, incident: Incident) -> None:
        await post_triage(self.cfg.slack_webhook_url, incident)
        if self.cfg.pagerduty_routing_key:
            sev = incident.classification.severity if incident.classification else "unknown"
            if sev in ("critical", "high"):
                from .pagerduty import trigger_incident
                await trigger_incident(self.cfg.pagerduty_routing_key, incident)


# ── Prompt construction ────────────────────────────────────────────────────

def _build_triage_prompt(incident: Incident) -> str:
    metrics_lines = []
    for key, val in incident.metrics.items():
        metrics_lines.append(f"  {key}: {val:.2f}" if isinstance(val, float) else f"  {key}: {val}")
    metrics_str = "\n".join(metrics_lines) if metrics_lines else "  (no metrics available)"

    # Use log tail — recent lines are most likely to contain the error
    log_tail = "\n".join(incident.logs.splitlines()[-40:]) if incident.logs else "(no logs available)"

    return f"""You are an SRE diagnosing a production incident. Respond ONLY with valid JSON — no markdown, no prose, no explanation.

INCIDENT:
  alert: {incident.alert_name}
  pod: {incident.namespace}/{incident.pod}
  fired_at: {incident.starts_at}

METRICS:
{metrics_str}

RECENT LOGS (last 40 lines):
{log_tail}

Respond with this exact JSON schema:
{{
  "category":        one of ["oom","cuda_error","model_crash","request_spike","slow_ttft","queue_buildup","healthy","unknown"],
  "severity":        "low" | "medium" | "high" | "critical",
  "summary":         "1-2 sentence diagnosis",
  "recommendation":  "specific next action for the on-call engineer",
  "auto_restartable": true | false
}}"""

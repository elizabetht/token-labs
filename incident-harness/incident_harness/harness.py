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


# ── Prompt construction ────────────────────────────────────────────────────
#
# TODO(human): implement _build_triage_prompt(incident) below.
#
# This function receives a fully-enriched Incident and must return a string
# prompt that gives the LLM everything it needs to classify the incident.
#
# The LLM must respond with JSON matching this schema:
#   {
#     "category":        one of ["oom","cuda_error","model_crash","request_spike",
#                                "slow_ttft","queue_buildup","healthy","unknown"],
#     "severity":        "low" | "medium" | "high" | "critical",
#     "summary":         "1-2 sentence diagnosis",
#     "recommendation":  "specific next action for the on-call engineer",
#     "auto_restartable": true | false
#   }
#
# What to include in the prompt:
#   - Alert name + pod identity
#   - Key metrics from incident.metrics (gpu_util_pct, gpu_mem_used_gb,
#     request_rate_1m, p99_ttft_ms, queue_depth)
#   - Recent log lines from incident.logs
#   - Clear instruction to respond ONLY with JSON (no markdown, no prose)
#
# Trade-offs to consider:
#   - How much of the log to include? More context = better diagnosis,
#     but long logs eat into the context window and slow the LLM.
#   - How to format metrics? Raw floats vs human-readable ("4.2 GB used")?
#   - Should you include the alert labels dict? Could confuse the LLM with noise.
#   - Log tail vs log head? (The most recent lines usually have the error.)

def _build_triage_prompt(incident: Incident) -> str:
    raise NotImplementedError(
        "Implement _build_triage_prompt(). See the TODO above for the full spec."
    )

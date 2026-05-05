"""
slack.py — post the triage report to Slack as a Block Kit message

Block Kit gives us structured sections, code blocks, and colour-coded
severity indicators. The on-call engineer gets everything in one message:
alert context, LLM diagnosis, and what action was taken.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

SEVERITY_EMOJI = {
    "low":      ":large_blue_circle:",
    "medium":   ":large_yellow_circle:",
    "high":     ":large_orange_circle:",
    "critical": ":red_circle:",
}


async def post_triage(webhook_url: str, incident) -> None:
    """
    Send a structured Slack message for the completed triage.
    `incident` is the Incident dataclass from harness.py.
    """
    sev_emoji = SEVERITY_EMOJI.get(incident.classification.severity, ":white_circle:")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{sev_emoji}  Incident: {incident.alert_name}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Pod*\n`{incident.namespace}/{incident.pod}`"},
                {"type": "mrkdwn", "text": f"*Severity*\n{incident.classification.severity.upper()}"},
                {"type": "mrkdwn", "text": f"*Category*\n`{incident.classification.category}`"},
                {"type": "mrkdwn", "text": f"*Fired at*\n{incident.starts_at}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*:mag: Diagnosis*\n{incident.classification.summary}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*:wrench: Recommendation*\n{incident.classification.recommendation}",
            },
        },
    ]

    if incident.action_taken:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*:robot_face: Action taken*\n`{incident.action_taken}`",
            },
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": "_incident-harness · token-labs_"},
        ],
    })

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json={"blocks": blocks})
            resp.raise_for_status()
    except Exception as e:
        log.error("Slack post failed: %s", e)

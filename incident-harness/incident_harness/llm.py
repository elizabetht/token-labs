"""
llm.py — LLM classification call

Single responsibility: send a structured prompt to the vLLM endpoint,
parse the JSON response into an IncidentClassification.

Why JSON output? We need machine-readable severity + action fields so the
ACT state can make deterministic decisions. Free-form text would require
another LLM call to parse — that's the trap of letting LLMs orchestrate.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

# Categories the LLM must choose from — keeps the action space bounded
CATEGORIES = [
    "oom",            # GPU/CPU out of memory
    "cuda_error",     # CUDA runtime failure
    "model_crash",    # vLLM/SGLang process crash
    "request_spike",  # unusual traffic surge
    "slow_ttft",      # time-to-first-token degradation
    "queue_buildup",  # request queue growing unchecked
    "healthy",        # false positive / alert resolved
    "unknown",        # cannot determine from available context
]


@dataclass
class IncidentClassification:
    category: str      # one of CATEGORIES
    severity: str      # "low" | "medium" | "high" | "critical"
    summary: str       # 1-2 sentence human-readable diagnosis
    recommendation: str  # specific next action for the on-call engineer
    auto_restartable: bool  # harness can restart the pod to resolve this


async def classify(
    llm_url: str,
    model: str,
    prompt: str,
) -> IncidentClassification:
    """
    POST the triage prompt to the vLLM endpoint, parse the JSON response.

    Falls back to a safe "unknown" classification if the LLM is unreachable
    or returns malformed output. Never raises — the harness must keep running.
    """
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an on-call SRE for a GPU inference cluster. "
                    "Analyze the incident context and respond ONLY with valid JSON. "
                    f"The 'category' field must be one of: {', '.join(CATEGORIES)}."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 512,
        "temperature": 0.1,   # low temperature — we want consistent, factual output
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{llm_url}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]

        # Strip any <think>...</think> blocks (Qwen reasoning mode)
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

        # Extract JSON — handle LLMs that wrap it in ```json ... ```
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object in LLM response: {raw[:200]}")

        data = json.loads(match.group())
        return IncidentClassification(
            category=data.get("category", "unknown"),
            severity=data.get("severity", "medium"),
            summary=data.get("summary", ""),
            recommendation=data.get("recommendation", ""),
            auto_restartable=bool(data.get("auto_restartable", False)),
        )

    except Exception as e:
        log.error("LLM classification failed: %s", e)
        return IncidentClassification(
            category="unknown",
            severity="medium",
            summary=f"LLM classification unavailable: {e}",
            recommendation="Investigate manually — check pod logs and GPU metrics.",
            auto_restartable=False,
        )

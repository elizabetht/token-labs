"""
tools.py — deterministic tool functions (no LLM involvement)

Each tool is a plain async function: input → output.
The harness calls these explicitly during the ENRICH phase.

Design principle: tools are dumb. They fetch data, nothing else.
The LLM receives their output but cannot invoke them — that prevents
the class of bugs where an LLM hallucinates tool calls in a loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from kubernetes import client as k8s_client, config as k8s_config

log = logging.getLogger(__name__)


async def query_loki(
    loki_url: str,
    namespace: str,
    pod: str,
    minutes_back: int = 15,
    limit: int = 80,
) -> str:
    """
    Fetch recent log lines for a pod from Loki.

    Uses LogQL: {namespace="...", pod="..."} — pulls `limit` lines from
    the last `minutes_back` minutes.

    Returns a plain-text string ready to paste into the LLM prompt.
    Empty string if the pod has no logs or Loki is unreachable.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes_back)

    params = {
        "query": f'{{namespace="{namespace}", pod="{pod}"}}',
        "start": str(int(start.timestamp() * 1e9)),   # nanoseconds
        "end":   str(int(now.timestamp() * 1e9)),
        "limit": str(limit),
        "direction": "backward",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{loki_url}/loki/api/v1/query_range", params=params)
            resp.raise_for_status()
            data = resp.json()

        lines = []
        for stream in data.get("data", {}).get("result", []):
            for ts, line in stream.get("values", []):
                lines.append(line)

        # Loki returns newest-first when direction=backward; reverse for readability
        lines.reverse()
        return "\n".join(lines[-limit:]) if lines else "(no logs found)"

    except Exception as e:
        log.warning("Loki query failed: %s", e)
        return f"(loki unavailable: {e})"


async def query_prometheus(
    prometheus_url: str,
    pod: str,
    namespace: str,
) -> dict:
    """
    Pull key vLLM metrics for the incident pod from Prometheus.

    Returns a dict of metric_name → latest value.
    Falls back to empty dict if Prometheus is unreachable.
    """
    queries = {
        "gpu_util_pct":     f'avg(DCGM_FI_DEV_GPU_UTIL{{pod="{pod}"}})',
        "gpu_mem_used_gb":  f'avg(DCGM_FI_DEV_FB_USED{{pod="{pod}"}}) / 1024',
        "request_rate_1m":  f'rate(vllm:request_success_total{{pod="{pod}"}}[1m])',
        "p99_ttft_ms":      f'histogram_quantile(0.99, rate(vllm:time_to_first_token_seconds_bucket{{pod="{pod}"}}[5m])) * 1000',
        "queue_depth":      f'vllm:num_requests_waiting{{pod="{pod}"}}',
    }

    results = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, promql in queries.items():
            try:
                resp = await client.get(
                    f"{prometheus_url}/api/v1/query",
                    params={"query": promql},
                )
                resp.raise_for_status()
                data = resp.json()
                result = data.get("data", {}).get("result", [])
                if result:
                    results[name] = float(result[0]["value"][1])
            except Exception as e:
                log.debug("Prometheus query '%s' failed: %s", name, e)
                results[name] = None

    return results


def restart_pod(namespace: str, pod: str) -> str:
    """
    Delete the pod so Kubernetes reschedules it (bare pods don't respawn —
    this is only useful for pods managed by a Deployment/StatefulSet/DaemonSet).

    Returns a human-readable result string.
    """
    try:
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()

        v1 = k8s_client.CoreV1Api()
        v1.delete_namespaced_pod(name=pod, namespace=namespace)
        return f"deleted pod {namespace}/{pod} — controller will reschedule"
    except Exception as e:
        return f"restart failed: {e}"

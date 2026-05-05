"""config.py — all settings from environment variables."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    # Observability stack (in-cluster ClusterIP addresses)
    loki_url: str
    prometheus_url: str

    # LLM endpoint (vLLM, OpenAI-compatible)
    llm_url: str
    llm_model: str

    # Slack incoming webhook URL
    slack_webhook_url: str

    # Triage behaviour
    log_lines: int = 80       # how many recent log lines to feed the LLM
    auto_restart: bool = False # set True to allow harness to kubectl-restart pods

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            loki_url=os.environ.get("LOKI_URL", "http://loki.monitoring.svc.cluster.local:3100"),
            prometheus_url=os.environ.get("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"),
            llm_url=os.environ.get("LLM_URL", "http://192.168.1.207:8000"),
            llm_model=os.environ.get("LLM_MODEL", "Qwen/Qwen3.5-27B-GPTQ-Int4"),
            slack_webhook_url=os.environ["SLACK_WEBHOOK_URL"],  # required
            log_lines=int(os.environ.get("LOG_LINES", "80")),
            auto_restart=os.environ.get("AUTO_RESTART", "false").lower() == "true",
        )

# Shared inference platform

This directory configures shared services consumed by model workloads:

- `gateway/`: Gateway API and Envoy AI Gateway instances and policies.
- `llm-d/`: shared llm-d infrastructure; model-specific releases stay in `models/`.
- `policies/` and `tenants/`: authentication, rate limits, and tenant configuration.
- `model-aggregator/`: shared model routing/aggregation service.
- `monitoring/` and `observability/`: platform telemetry.

It does not install cluster operators or deploy model workers.

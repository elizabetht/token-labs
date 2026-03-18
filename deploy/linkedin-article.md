# Multi-Tenant LLM Inference Without Building a Platform Team

*A production stack that handles authentication, billing, and GPU-aware routing — deployed entirely through Kubernetes CRDs, with no custom application code.*

---

Every enterprise exploring private LLM deployment hits the same inflection point. The model runs. A prototype works. Then someone asks: "How do we give five teams access to this without them stepping on each other?"

That question typically triggers a 6-month platform buildout — a custom API gateway, a billing service, an identity provider, a load balancer with GPU awareness, and a team to maintain all of it.

There is another path. By composing four open-source projects — each backed by CNCF governance — it is possible to stand up a fully multi-tenant LLM inference platform where every policy is a Kubernetes resource. No custom services. No databases. No middleware to maintain.

Here is how it works, what it costs, and why it matters for organizations scaling AI infrastructure.

---

## The Business Problem

Running LLMs on private infrastructure is straightforward for a single team. Multi-tenancy is where complexity explodes:

- **Authentication and authorization** — validating callers and enforcing access controls per model
- **Tiered rate limiting** — different throughput guarantees for different teams or business units
- **Token-level billing** — tracking actual GPU consumption, not just request counts, against departmental budgets
- **Intelligent GPU scheduling** — routing requests to the right GPU pod based on real-time memory and compute state, not round-robin

The conventional approach is to build a proxy service that handles all of this. That proxy becomes the most critical — and most fragile — component in the stack. It requires dedicated engineering, on-call coverage, and accumulates technical debt that compounds with every new tenant.

---

## The Architecture: Four Projects, Zero Custom Code

| Concern | Project | Role |
|---------|---------|------|
| Traffic management | **Envoy Gateway** | L7 proxy implementing the Kubernetes Gateway API standard |
| Model routing | **Envoy AI Gateway** | Extracts model names from OpenAI-compatible requests and routes to the correct backend |
| Auth, rate limits, token billing | **Kuadrant** | Policy-as-CRD: API key validation, per-tenant request quotas, per-tenant token budgets |
| Inference scheduling | **llm-d** | GPU-aware pod selection based on KV-cache pressure, prefix-cache locality, and queue depth |

The control plane is Kubernetes itself. Every policy — who can call what, how much they can use, and how requests are routed — is expressed in version-controlled YAML.

---

## What Happens When a Request Arrives

A standard OpenAI-compatible API call flows through seven stages before and after GPU execution:

1. **Envoy Gateway** receives the request and triggers two policy checks — authentication and rate limiting — both defined as CRDs attached to the Gateway resource.

2. **Authorino** validates the API key by looking up a Kubernetes Secret, then extracts the caller's tier (free, pro, or enterprise) and tenant ID from the Secret's annotations. This identity context flows through the entire pipeline.

3. **Limitador** checks per-tenant request counters stored in Redis against tier-based quotas. Exceeding the limit returns a standard `429 Too Many Requests`. The entire rate limit policy is a single Kubernetes resource.

4. **Envoy AI Gateway** parses the `"model"` field from the JSON request body, sets a routing header, and directs the request to the correct model backend. This eliminates the need for a custom body-parsing router.

5. **llm-d's Endpoint Picker** scores every available GPU pod on three signals — KV-cache memory utilization, prefix-cache hit potential, and in-flight queue depth — then selects the optimal pod. Prefix-cache routing alone can halve effective compute cost on repetitive workloads (system prompts, RAG pipelines).

6. **vLLM** executes inference on the selected GPU and returns a response including token usage metadata.

7. **Kuadrant's response filter** reads `usage.total_tokens` from the response body and counts it against the tenant's token budget. The `TokenRateLimitPolicy` CRD defines daily and per-minute token quotas per tier — no billing service required.

---

## Tenant Lifecycle: Self-Service in Seconds

Onboarding a new tenant is a single `kubectl apply` that creates a Kubernetes Secret with the API key and tier annotation. The key is live immediately — no restart, no configuration reload, no ticket to a platform team.

Changing a tenant's tier is an annotation update. Revoking access is deleting the Secret. The identity store is Kubernetes itself — no external identity provider, no database migration.

This matters for organizations where provisioning speed directly affects time-to-value for internal AI adoption.

---

## Why This Matters for Engineering and Business Leaders

### GPU costs are the largest line item — routing intelligence directly reduces spend
Round-robin load balancing wastes GPU cycles. llm-d's inference-aware scheduling keeps KV-cache utilization balanced, reuses prefix computations across similar requests, and avoids routing to memory-pressured pods. The result is measurably higher throughput from the same hardware.

### Operational surface area is the real risk
Every custom service is a service that can fail, that needs on-call coverage, and that drifts from its documentation. When authentication, billing, and routing are Kubernetes CRDs, they share the same operational model as every other resource in the cluster. The infrastructure team already knows how to manage, monitor, and audit them.

### Declarative policy is auditable and governable
Every quota, every access rule, every rate limit lives in version-controlled YAML. Changing a free-tier token budget from 50,000 to 75,000 tokens per day is a three-character git commit that security, finance, and compliance teams can review in a pull request. There is no admin console to screenshot, no database row to query.

### Standards-based architecture preserves optionality
The stack is built on the Kubernetes Gateway API — the successor to Ingress and the emerging standard for traffic management. Envoy Gateway implements it. Kuadrant attaches policies to it. llm-d extends it with the InferencePool CRD. This means the policy layer and inference scheduling are portable — the data plane can be swapped without rewriting policies.

### Build vs. buy is the wrong framing
The real question is: *what should your team's job be?* If the answer is model quality, latency optimization, and cost management — not API gateway development — then composing proven open-source projects with strong governance is the higher-leverage choice.

---

## Production Numbers

Running on three NVIDIA DGX Spark nodes (one ARM64 CPU controller, two GB10 GPU workers):

| Metric | Value |
|--------|-------|
| Prefill throughput | 3,203 tokens/sec |
| Decode throughput | 520 tokens/sec |
| Cost per 1M input tokens | $0.006 |
| Cost per 1M output tokens | $0.037 |

Two models served simultaneously: Llama 3.1 8B Instruct at 80% GPU utilization and Nemotron VL 12B (vision-language, FP8 quantized) at 90% GPU utilization. Text-to-speech runs alongside the LLM on a shared node. The entire platform fits on hardware that sits on a desk.

---

## What Was Deliberately Not Built

- No custom API gateway service
- No identity database or external identity provider
- No billing or metering microservice
- No response-parsing middleware
- No custom load balancer logic
- No configuration reload pipelines

Each of these would have been the default answer three years ago. The CNCF ecosystem and the Kubernetes Gateway API have made them unnecessary for this class of workload.

---

## Architecture Overview

```
Client (Authorization: Bearer <api-key>)
  │
  ▼
Envoy Gateway — L7 proxy, Kubernetes Gateway API
  ├── Authorino (ext_auth) — API key validation via K8s Secrets
  ├── Limitador (rate limit) — per-tenant request quotas
  │
  ├── Envoy AI Gateway — reads JSON body, routes by model name
  │   ├── model=Llama-3.1-8B      → InferencePool → llm-d EPP → vLLM (GPU)
  │   └── model=Nemotron-VL-12B   → InferencePool → llm-d EPP → vLLM (GPU)
  │
  └── HTTPRoute: /v1/audio/speech  → Magpie TTS
  │
  ▼
Response path:
  └── Kuadrant WASM shim — parses usage.total_tokens → Limitador token quota
```

The full source, deployment scripts, and benchmark data are available at [github.com/elizabetht/token-labs](https://github.com/elizabetht/token-labs).

---

*Built on NVIDIA DGX Spark with MicroK8s, Envoy AI Gateway, Envoy Gateway, Kuadrant, llm-d, and vLLM.*

*If your organization is evaluating private LLM inference infrastructure, I'd welcome the conversation.*

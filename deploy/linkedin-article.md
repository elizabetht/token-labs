# Zero Custom Code Multi-Tenant LLM Inference on DGX Spark

The default path to multi-tenant LLM serving looks something like this: team gets vLLM running on a GPU, demo works, leadership wants five teams on it by next quarter. Suddenly there's a 6-month platform buildout — custom API gateway, billing service, identity provider, GPU-aware load balancer, and 2-3 engineers maintaining all of it. This is a solved problem being re-solved inside every organization that deploys LLMs privately.

It doesn't need to be. Four open-source projects — Envoy Gateway, Envoy AI Gateway, Kuadrant, and llm-d — compose into a fully multi-tenant inference platform where every policy is a Kubernetes CRD. No custom services. No databases. No middleware. Zero lines of application code. We built this on two NVIDIA DGX Spark GB10 nodes and a MicroK8s cluster, and it works.


## The actual problem

Single-team LLM serving is trivial. Multi-tenancy is where everything breaks.

You need per-caller auth. Per-tenant rate limits — not just request counts, but actual token consumption against budgets. Model routing that reads the request body and sends traffic to the right GPU pool. And load balancing that isn't round-robin, because round-robin on GPU inference is burning money — it ignores KV-cache pressure entirely and routes to pods that are already memory-saturated.

The traditional answer is a proxy service that handles all of this. That proxy becomes the most critical and most fragile thing in the stack. It needs on-call coverage, it accumulates tech debt with every new tenant, and it's a platform nobody set out to build.

There's a better way now.


## The stack

Envoy Gateway is the L7 proxy, implementing the Kubernetes Gateway API. Envoy AI Gateway sits behind it as an ext_proc filter — it reads the `"model"` field from the OpenAI-compatible request body, sets a routing header, and directs traffic to the right InferencePool backend. No custom body parser.

Kuadrant does the policy layer. Its Authorino component validates API keys stored as Kubernetes Secrets and extracts tenant metadata (tier, user-id) from Secret annotations. Its Limitador component enforces per-tenant request quotas and — critically — per-tenant token budgets by parsing `usage.total_tokens` from the response body. A free tier gets 50k tokens/day. A pro tier gets 200k. The entire policy is one CRD.

llm-d provides inference-aware scheduling. Its Endpoint Picker (EPP) runs as a second ext_proc filter and scores every vLLM pod on three signals before routing: KV-cache memory utilization, prefix-cache hit potential, and in-flight queue depth. This is materially better than least-connections. Prefix-cache routing alone can halve effective compute cost on RAG workloads with stable system prompts.

Four projects, zero custom code. The control plane is Kubernetes.


## Request path, end to end

A single API call touches 7 stages:

1. Envoy Gateway receives the request. Kuadrant's AuthPolicy triggers Authorino, which validates the API key by looking up a Kubernetes Secret, extracts the tenant's tier from its annotations, and passes that identity context downstream. This is the critical detail — Envoy Gateway has built-in API key auth, but it only validates the key. It doesn't extract tenant metadata. Without metadata, every tenant looks identical to the rate limiter.

2. Limitador checks per-tenant request counters against tier-based quotas. Exceeds the limit, returns 429. One CRD.

3. Envoy AI Gateway's ext_proc parses the `"model"` field from the JSON body, sets `x-ai-eg-model` header.

4. AIGatewayRoute matches on that header and routes to the correct InferencePool — Llama 3.1 8B on spark-01 or Nemotron VL 12B on spark-02.

5. llm-d EPP scores all vLLM pods in the pool and picks the one with the best combination of KV-cache headroom, prefix-cache hit potential, and queue depth.

6. vLLM executes inference on the DGX Spark GB10 GPU. Returns the response with token usage metadata.

7. On the way back, Kuadrant's TokenRateLimitPolicy reads `usage.total_tokens` from the response body and counts it against the tenant's daily token budget via Limitador. No billing microservice.

Remove any one of these projects and custom code has to fill the gap. That's the point.


## Tenant onboarding is kubectl apply

Adding a new tenant is creating a Kubernetes Secret with an API key and a tier annotation. It's live immediately — no restart, no config reload, no ticket to a platform team.

Changing a tier is an annotation update. Revoking access is `kubectl delete secret`. Bumping a free-tier token budget from 50,000 to 75,000 tokens/day is a 2-character edit in a YAML file that goes through normal git review.

There is no identity database. Kubernetes is the identity store.


## Why this matters: GPU cost

GPUs are the largest line item in any inference deployment, and routing intelligence directly reduces spend. Round-robin wastes GPU cycles by sending requests to pods with saturated KV-cache. llm-d keeps utilization balanced, reuses prefix computations across similar prompts, and avoids memory-pressured pods. Same hardware, measurably higher throughput.

The operational angle is just as important. Every custom service is a service that can fail, needs on-call, and drifts from its docs. When auth, billing, and routing are CRDs, they share the same operational model as everything else in the cluster. The infra team already knows how to manage them.

The entire stack is built on the Kubernetes Gateway API standard. Envoy Gateway implements it. Kuadrant attaches policies to it. llm-d extends it with InferencePool. The data plane is swappable without rewriting policies. That portability matters in a landscape changing this fast.


## What wasn't built

No custom API gateway. No identity database. No billing service. No response-parsing middleware. No custom load balancer. No config reload pipelines.

Three years ago, every one of those was the default answer. The CNCF ecosystem made them unnecessary for this workload class.


## Where it breaks down

**CRD composition ceiling.** Per-tenant auth, rate limiting, token billing, and model routing all work as single-resource operations. Multi-model shared budgets, OAuth/OIDC for human users, and intra-tenant RBAC require coordinating multiple CRDs. Achievable, but the complexity jumps.

**OpenAI-compatible only.** Envoy AI Gateway routes by model name on OpenAI-compatible APIs. If you need Anthropic, Google, and local endpoints behind the same gateway, you're writing custom routing today.

**Streaming breaks token billing.** Kuadrant's WASM response filter parses the full response body. Streaming SSE makes that difficult — a failed filter means silent token undercounting, not a hard failure. This is the biggest gap for production chat workloads.

**Redis is the weak link for quotas.** Limitador fails open by default. Redis goes down, requests pass through and budgets go unenforced. Production needs Redis Sentinel or Cluster. Non-negotiable.

**Prefix-cache savings are workload-dependent.** High for RAG pipelines with repeated system prompts. Marginal for diverse, unique inputs. Don't assume the numbers generalize.


## Architecture overview

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

Full source, deployment scripts, and benchmark data: [github.com/elizabetht/token-labs](https://github.com/elizabetht/token-labs).

---

Open-source AI infrastructure doesn't stop at model weights. The serving, security, and metering layers need to be open too. The building blocks are here. The patterns exist across the organizations running inference at scale. They belong upstream, in the open, where they compound.

*Built on NVIDIA DGX Spark with MicroK8s, Envoy AI Gateway, Envoy Gateway, Kuadrant, llm-d, and vLLM.*

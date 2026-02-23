# We Built a Production LLM API Platform in a Weekend — With Zero Custom Middleware

*How composing four open-source projects replaced what used to require a dedicated platform team.*

---

There's a version of this story where we built a custom API gateway, wrote a billing service, stood up an identity provider, and hired someone to maintain all of it. That version takes months.

Here's what we actually did: deployed a fully multi-tenant LLM inference platform — authentication, rate limiting, token-based billing, and GPU-aware load balancing — with **no custom application code**. Every capability is a Kubernetes CRD. The entire policy surface is declarative YAML.

This is the architecture breakdown.

---

## The Problem We Were Solving

Running LLMs on private infrastructure sounds straightforward until you need to serve multiple tenants. Then you need:

- **Authentication** — who is this caller?
- **Authorization** — are they allowed to use this model?
- **Rate limiting** — how many requests per minute, at what tier?
- **Token quota enforcement** — not just request counts, but *actual token consumption* tracked against a billing budget
- **Intelligent routing** — not round-robin, but GPU-aware scheduling that maximizes throughput

The default answer is to write a proxy service that sits in front of your inference workers and handles all of this. That service then becomes the most critical piece of infrastructure you own, and also the one with the least test coverage.

We took a different approach.

---

## The Stack

Four open-source projects, each owning exactly one concern:

| Layer | Project | What it does |
|-------|---------|-------------|
| Data plane | **Envoy Gateway** | L7 proxy, TLS, Gateway API |
| AI routing | **Envoy AI Gateway** | Parses OpenAI request bodies, routes by model |
| Auth + quotas | **Kuadrant** | API key auth, rate limits, token billing |
| Inference scheduling | **llm-d** | GPU-aware pod selection |

No custom services. No databases. The control plane *is* Kubernetes.

---

## How a Request Flows

A client sends a standard OpenAI-compatible request:

```
POST /v1/chat/completions
Authorization: Bearer tlabs_sk_acme_...
{"model": "nvidia/Llama-3.1-Nemotron-Nano-8B-v1", "messages": [...]}
```

Here is what happens before a single GPU cycle fires:

**Step 1 — Envoy Gateway receives the request** and runs two filters in sequence: an external auth check and a rate limit check. Both are declarative — no code, just CRDs attached to the Gateway resource.

**Step 2 — Authorino validates the API key.** It scans Kubernetes Secrets labeled for the platform, finds the one matching the bearer token, and extracts two pieces of metadata from the Secret's annotations: the tenant's tier (`free`, `pro`, `enterprise`) and their unique ID. This context travels with the request for the rest of the pipeline.

**Step 3 — Limitador enforces request-count limits.** It uses the tier and tenant ID injected by Authorino to check counters in Redis. A free-tier tenant is capped at 10 requests/minute and 100/day. Pro is 100/min, 5,000/day. Enterprise is 1,000/min, 50,000/day. Breach the limit: `HTTP 429`. No custom middleware evaluated this — Kuadrant's `RateLimitPolicy` CRD expressed the entire policy.

**Step 4 — Envoy AI Gateway reads the JSON body.** It parses the `"model"` field, sets an `x-ai-eg-model` header, and the `AIGatewayRoute` uses that header to select the correct `InferencePool`. This sounds small but it's what eliminates the old "body-based router" pattern — a separate ext_proc sidecar with ConfigMaps and custom logic. EAG does it natively.

**Step 5 — llm-d's Endpoint Picker selects a vLLM pod.** This is where the architecture earns its keep at scale. Rather than round-robin, the EPP scores every pod in the pool on three signals simultaneously: KV-cache memory pressure, prefix-cache locality (routing similar prompts to reuse cached computations), and queue depth. The pod selection happens in microseconds, but the throughput impact is significant — prefix-cache reuse alone can halve effective compute cost on repetitive workloads.

**Step 6 — vLLM runs inference and returns a response.** The response body contains `usage.total_tokens`.

**Step 7 — Kuadrant's WASM shim reads the response body** and sends the token count to Limitador as a deferred billing event. The `TokenRateLimitPolicy` CRD defines per-tier daily token budgets (50K for free, 500K for pro, 5M for enterprise). No response parsing code. No billing service. The policy is eight lines of YAML.

---

## Tenant Onboarding in 30 Seconds

Here is the entire tenant provisioning flow:

```bash
kubectl create secret generic tenant-acme \
  --from-literal=api_key="tlabs_sk_$(openssl rand -hex 24)" \
  -n kuadrant-system \
  --dry-run=client -o yaml \
  | kubectl annotate --local -f - \
    kuadrant.io/groups=pro \
    secret.kuadrant.io/user-id=acme \
    -o yaml \
  | kubectl apply -f -
```

The key is live immediately. No restart. No config reload. No ticket to the platform team. Authorino watches for labeled Secrets via the Kubernetes API and picks up the new tenant in real time.

Changing a tenant's tier is a single annotation update. Revoking access is `kubectl delete secret`. The identity store *is* Kubernetes — no external IdP, no database migration, no Keycloak.

---

## What This Means for Engineering Leadership

**Build vs. buy framing is the wrong question.** The real question is: *what is your team's job?* If you are running an LLM platform, your team's job is model quality, latency, and cost — not writing API gateway middleware. Composing open-source projects with strong CNCF governance lets you stay focused.

**Operational surface area is the actual risk.** A custom billing service is a service that can fail, that needs on-call coverage, that accumulates technical debt. A `TokenRateLimitPolicy` CRD is a Kubernetes resource — it has the same operational model as everything else you already run.

**Declarative policy is auditable policy.** Every auth rule, every rate limit, every quota is expressed in version-controlled YAML. The diff between "free tier allows 50K tokens/day" and "free tier allows 75K tokens/day" is a three-character git commit. Security and finance teams can read it.

**The Kubernetes Gateway API is becoming the standard.** The [Gateway API](https://gateway-api.sigs.k8s.io/) is the successor to Ingress. Envoy Gateway implements it. Kuadrant policies attach to it. llm-d's InferencePool extends it. Building on this standard means your policies, routes, and backends are all portable — swap the data plane later without rewriting your policy layer.

---

## The Numbers

Running on three NVIDIA DGX Spark nodes (one CPU controller, two GB10 GPU workers):

| Metric | Value |
|--------|-------|
| Prefill throughput | 3,203 tokens/sec |
| Decode throughput | 520 tokens/sec |
| Cost per 1M input tokens | $0.006 |
| Cost per 1M output tokens | $0.037 |

Two models served simultaneously: Nemotron-Llama 8B on spark-01 (80% GPU utilization, BF16) and Nemotron VL 12B FP8 on spark-02 (90% GPU utilization, vision-language). Magpie TTS runs in CPU mode on spark-01 alongside the LLM — the GPU is fully committed to vLLM.

---

## What We Did Not Build

It is worth being explicit about what the architecture deliberately avoids:

- No custom API gateway service
- No identity database or external IdP
- No billing microservice
- No response-parsing middleware
- No custom load balancer logic
- No config reload pipelines

Every one of these would have been the default answer five years ago. The CNCF ecosystem has made them unnecessary.

---

## The Full Architecture

```
Client (Authorization: Bearer <api-key>)
  │
  ▼
Envoy Gateway — L7 proxy, Gateway API
  ├── Authorino (ext_auth) — API key validation via K8s Secrets
  ├── Limitador (rate limit) — per-tenant request quotas
  │
  ├── Envoy AI Gateway — reads JSON body, routes by model name
  │   ├── model=Nemotron-Llama-8B  → InferencePool → llm-d EPP → vLLM (spark-01)
  │   └── model=Nemotron-VL-12B   → InferencePool → llm-d EPP → vLLM (spark-02)
  │
  ├── HTTPRoute: /v1/audio/speech  → Magpie TTS (CPU, spark-01)
  └── HTTPRoute: /v1/audio/transcriptions → Riva STT (NVIDIA NIM proxy)
  │
  ▼
Response filters:
  └── Kuadrant WASM shim — parses usage.total_tokens → Limitador token quota
```

The full source, deployment scripts, and benchmark data are open on GitHub: [github.com/elizabetht/token-labs](https://github.com/elizabetht/token-labs)

---

*Built on NVIDIA DGX Spark with MicroK8s, Envoy AI Gateway, Envoy Gateway, Kuadrant, llm-d, and vLLM.*

*If you are working on inference infrastructure or LLM platform engineering, I would enjoy comparing notes.*

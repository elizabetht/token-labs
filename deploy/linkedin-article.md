# Multi-Tenant LLM Inference, Assembled Entirely from Open Source

Every enterprise exploring private LLM deployment hits the same inflection point. The model runs. A prototype works. Then someone asks: "How do we give five teams access to this without them stepping on each other?"

That question has historically triggered a 6-month platform buildout — a custom API gateway, a billing service, an identity provider, a GPU-aware load balancer, and a team to maintain all of it. But if the open-source AI revolution means anything, it means this infrastructure shouldn't need to be rebuilt from scratch inside every organization.

> "The question isn't whether to build a multi-tenant inference platform. It's whether anyone should be writing the code for it."

By composing four open-source projects — each backed by CNCF governance — it is possible to stand up a fully multi-tenant LLM inference platform where every policy is a Kubernetes resource. No custom services. No databases. No middleware to maintain. Here is what that looks like in practice, and why it matters for organizations scaling AI infrastructure.


## Where complexity actually lives

Running LLMs on private infrastructure is straightforward for a single team. Multi-tenancy is where the assumptions break.

It requires validating callers and enforcing access controls per model. It demands different throughput guarantees for different teams or business units. It means tracking actual GPU consumption — not just request counts — against departmental budgets. And it means routing requests to the right GPU pod based on real-time memory and compute state, not round-robin.

The conventional response is to build a proxy service that handles all of this. That proxy becomes the most critical — and most fragile — component in the stack. It requires dedicated engineering, on-call coverage, and accumulates technical debt that compounds with every new tenant. It is, in every meaningful sense, a platform nobody intended to build.

The CNCF ecosystem now offers a different path.


## Composing the stack

Envoy Gateway provides the L7 proxy layer, implementing the Kubernetes Gateway API standard — the successor to Ingress and the emerging foundation for traffic management. Envoy AI Gateway sits behind it, parsing model names from OpenAI-compatible request bodies and routing to the correct backend. No custom body-parsing router required.

Kuadrant handles the policy layer: API key validation, per-tenant request quotas, and per-tenant token budgets — all expressed as Kubernetes CRDs. And llm-d provides GPU-aware inference scheduling, selecting pods based on KV-cache pressure, prefix-cache locality, and queue depth rather than naive load distribution.

> "Access policies, rate limits, routing rules — every piece of configuration that would normally live in a custom service lives in version-controlled YAML instead."

The control plane is Kubernetes itself. Four projects, zero custom code.


## The request path

A standard OpenAI-compatible API call flows through seven stages before and after GPU execution. Understanding this path explains why each project exists and why removing any one of them would force custom code back into the stack.

Envoy Gateway receives the request and triggers two policy checks — authentication and rate limiting — both defined as CRDs attached to the Gateway resource. Authorino, Kuadrant's auth component, validates the API key by looking up a Kubernetes Secret, then extracts the caller's tier and tenant ID from the Secret's annotations. This identity context flows through the entire pipeline — it is what makes per-tenant rate limiting and token billing possible in later stages. Envoy Gateway has built-in API key authentication, but it only validates the key. It does not extract tenant metadata or pass identity context downstream. Without that context, every tenant looks the same to the rate limiter.

Limitador then checks per-tenant request counters stored in Redis against tier-based quotas. Exceeding the limit returns a standard 429. The entire rate limit policy is a single Kubernetes resource.

From there, Envoy AI Gateway parses the model field from the JSON request body, sets a routing header, and directs the request to the correct model backend. llm-d's Endpoint Picker scores every available GPU pod on three signals — KV-cache memory utilization, prefix-cache hit potential, and in-flight queue depth — then selects the optimal pod. Prefix-cache routing alone can halve effective compute cost on repetitive workloads like system prompts and RAG pipelines.

vLLM executes inference on the selected GPU and returns a response with token usage metadata. On the way back, Kuadrant's response filter reads the total token count from the response body and counts it against the tenant's token budget. Daily and per-minute token quotas per tier are defined in a single CRD — no billing service required.


## Tenant onboarding as a kubectl apply

Onboarding a new tenant is a single command that creates a Kubernetes Secret with the API key and tier annotation. The key is live immediately — no restart, no configuration reload, no ticket to a platform team.

Changing a tenant's tier is an annotation update. Revoking access is deleting the Secret. The identity store is Kubernetes itself. This matters for organizations where provisioning speed directly affects time-to-value for internal AI adoption — and where the alternative is a weeks-long procurement cycle through a central platform team.


## Why this architecture matters

GPU costs are the largest line item in any inference deployment, and routing intelligence directly reduces that spend. Round-robin load balancing wastes GPU cycles. llm-d's inference-aware scheduling keeps KV-cache utilization balanced, reuses prefix computations across similar requests, and avoids routing to memory-pressured pods. The result is measurably higher throughput from the same hardware.

But the operational surface area may be the more important consideration. Every custom service is a service that can fail, that needs on-call coverage, and that drifts from its documentation. When authentication, billing, and routing are Kubernetes CRDs, they share the same operational model as every other resource in the cluster. The infrastructure team already knows how to manage, monitor, and audit them.

> "Changing a free-tier token budget from 50,000 to 75,000 tokens per day is a two-character git commit that security, finance, and compliance teams can review in a pull request."

The stack is built on the Kubernetes Gateway API. Envoy Gateway implements it. Kuadrant attaches policies to it. llm-d extends it with the InferencePool CRD. This means the policy layer and inference scheduling are portable — the data plane can be swapped without rewriting policies. Standards-based architecture preserves optionality in a landscape that is moving fast.


## What was deliberately not built

No custom API gateway service. No identity database. No billing or metering microservice. No response-parsing middleware. No custom load balancer logic. No configuration reload pipelines.

Each of these would have been the default answer three years ago. The CNCF ecosystem and the Kubernetes Gateway API have made them unnecessary for this class of workload. The real question isn't build versus buy — it's *what should the team's job be?* If the answer is model quality, latency optimization, and cost management, then composing proven open-source projects with strong governance is the higher-leverage choice.


## Trade-offs worth naming

### CRD composition ceiling
Per-tenant auth, rate limiting, token billing, and model routing work cleanly. Multi-model shared budgets, OAuth/OIDC for human users, and intra-tenant RBAC all require coordinating multiple CRDs — achievable, but no longer single-resource operations.

### OpenAI-compatible routing only
Envoy AI Gateway routes by model name on OpenAI-compatible APIs. Heterogeneous provider environments (Anthropic, OpenAI, local endpoints) require custom routing today.

### Streaming token billing
Kuadrant's WASM response filter works for non-streaming responses. Streaming SSE complicates body inspection — a failed or bypassed filter means silent undercounting, not a hard failure.

### Redis as quota state
Limitador fails open by default. A Redis outage means requests pass but budgets go unenforced. Production deployments need Redis Sentinel or Cluster.

### Prefix-cache savings are workload-dependent
High impact for RAG pipelines with stable system prompts, marginal for varied inputs.

These are trade-offs, not limitations — and naming them upfront is what lets teams adopt with confidence.

What can I write for what interests about LiteLLM given my work on tokenlabs?
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

The full source, deployment scripts, and benchmark data are available at [github.com/elizabetht/token-labs](https://github.com/elizabetht/token-labs).

---

Open-source AI doesn't stop at the model weights. The infrastructure to serve, secure, and meter those models needs to be open too — and the building blocks are already here. The patterns to solve multi-tenant inference exist across the organizations running AI at scale. They belong upstream, in the open, where they can compound.

*Built on NVIDIA DGX Spark with MicroK8s, Envoy AI Gateway, Envoy Gateway, Kuadrant, llm-d, and vLLM.*

*Organizations evaluating private LLM inference infrastructure are welcome to connect.*

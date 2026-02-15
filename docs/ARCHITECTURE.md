# TokenLabs Multi-Tenant Inference Architecture

## Overview

TokenLabs provides multi-tenant LLM inference-as-a-service on a MicroK8s cluster
with DGX Spark GPU workers. The architecture composes three open-source projects
— each handling a distinct concern — with **zero custom application code**:

| Layer | Project | Responsibility |
|---|---|---|
| **Gateway** | [Envoy Gateway](https://gateway.envoyproxy.io/) | Kubernetes Gateway API implementation (data-plane proxy) |
| **AI Gateway** | [Envoy AI Gateway](https://aigateway.envoyproxy.io/) | AI-native routing — model extraction, token tracking, InferencePool integration |
| **Tenant Controls** | [Kuadrant](https://docs.kuadrant.io/) | API-key auth, per-tenant token-based rate limiting, request quotas |
| **Inference Routing** | [llm-d](https://llm-d.ai/) | KV-cache & LoRA-aware request scheduling via ext_proc |

All configuration is declarative Kubernetes CRDs — no FastAPI, no custom gateway code.

---

## Infrastructure

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MicroK8s Cluster                            │
│                                                                     │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐        │
│  │  controller     │  │  spark-01      │  │  spark-02      │        │
│  │  (CPU, ARM64)   │  │  (GB10 GPU)    │  │  (GB10 GPU)    │        │
│  │                 │  │  ARM64         │  │  ARM64         │        │
│  │  • Envoy GW     │  │  • vLLM pod    │  │  • vLLM pod    │        │
│  │  • Kuadrant     │  │    Llama 3.1   │  │    Nemotron VL │        │
│  │  • llm-d EPPs   │  │    8B Instruct │  │    12B FP8     │        │
│  │                 │  │  • Magpie TTS  │  │              │        │
│  │                 │  │    (GPU, 357M) │  │              │        │
│  └────────────────┘  └────────────────┘  └────────────────┘        │
└─────────────────────────────────────────────────────────────────────┘
```

- **Controller node**: Runs Envoy Gateway proxy, Kuadrant operators (Authorino + Limitador), llm-d EPPs (one per InferencePool)
- **spark-01**: vLLM serving `meta-llama/Llama-3.1-8B-Instruct` (text/chat LLM, 80% GPU utilization) + Magpie TTS (357M, GPU-accelerated)
- **spark-02**: vLLM serving `nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8` (vision-language model)

---

## Request Flow

```
Client (API key in header)
  │
  ▼
┌──────────────────────────────────────┐
│  Envoy Gateway (Gateway)             │  gatewayClassName: eg
│  ├─ Kuadrant WasmPlugin              │  (injected automatically)
│  │  ├─ AuthPolicy → Authorino        │  ① Validate API key, extract tenant tier
│  │  └─ RateLimitPolicy → Limitador   │  ② Check request-count limits
│  │                                    │
│  ├─ ext_proc → AI Gateway            │  ③ Read "model" from body, set header:
│  │  (Envoy AI Gateway controller)     │     x-ai-eg-model
│  │                                    │
│  ├─ AIGatewayRoute matches header     │  ④ Route to correct InferencePool
│  │  (x-ai-eg-model → pool backend)    │
│  │                                    │
│  ├─ ext_proc → llm-d EPP             │  ⑤ Inference-aware scheduling
│  │  (KV-cache hit, queue depth,       │     (picks optimal vLLM pod)
│  │   LoRA adapter awareness)          │
│  │                                    │
│  └─ Route to selected vLLM pod       │  ⑥ Forward to backend
└──────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────┐
│  vLLM Worker (DGX Spark)             │  inference execution
│  └─ Response with usage:             │
│     { "total_tokens": 150,           │
│       "prompt_tokens": 100,          │
│       "completion_tokens": 50 }      │
└──────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────┐
│  AI Gateway (llmRequestCosts)        │  ⑦ Extract token usage from response
│  + Kuadrant TokenRateLimitPolicy     │     Count against tenant quota
│  └─ Limitador                        │     (enforced on NEXT request)
│     (50k tokens/day free,            │
│      200k tokens/day pro)            │
└──────────────────────────────────────┘
  │
  ▼
Client receives response
```

### Step-by-step

1. **Authentication** — Kuadrant's `AuthPolicy` intercepts the request and sends a `CheckRequest` to Authorino. Authorino validates the API key (stored as a Kubernetes Secret), extracts tenant metadata (tier, user-id), and enriches the request context.

2. **Request Rate Limiting** — Kuadrant's `RateLimitPolicy` enforces per-tenant request-count limits (e.g., 100 req/min for free tier) via Limitador.

3. **Model Routing (AI Gateway)** — The Envoy AI Gateway controller runs as an ext_proc extension. It reads the `"model"` field from the JSON request body and sets the `x-ai-eg-model` header on the request.

4. **AIGatewayRoute Matching** — The AIGatewayRoute rules match on the `x-ai-eg-model` header to route the request to the correct InferencePool backend (e.g., Llama → `token-labs-pool`, Nemotron → `nemotron-vl-pool`).

5. **Inference Scheduling** — llm-d's EPP (Endpoint Picker) receives the request via Envoy's `ext_proc` filter. It inspects KV-cache hit rates, queue depths, and LoRA adapter availability across vLLM pods, then selects the optimal backend.

6. **Inference Execution** — The request is forwarded to the selected vLLM pod, which generates the completion and returns `usage.total_tokens` in the response.

7. **Token Quota Tracking** — Kuadrant's `TokenRateLimitPolicy` extracts `usage.total_tokens` from the response body and sends it to Limitador as `hits_addend`. The tenant's cumulative token usage is tracked per time window.

---

## CRD Inventory

### Envoy Gateway CRDs

| CRD | Purpose |
|---|---|
| `GatewayClass` | Defines `eg` as the gateway implementation |
| `Gateway` | Listener configuration (HTTP on port 80) |

### Envoy AI Gateway CRDs

| CRD | Purpose |
|---|---|
| `AIGatewayRoute` | Model-based routing to InferencePool backends with token tracking |

### Kuadrant CRDs

| CRD | API Group | Purpose |
|---|---|---|
| `Kuadrant` | `kuadrant.io/v1beta1` | Bootstraps Kuadrant components |
| `AuthPolicy` | `kuadrant.io/v1` | API-key auth with tier extraction |
| `RateLimitPolicy` | `kuadrant.io/v1` | Per-tenant request-count limits |
| `TokenRateLimitPolicy` | `kuadrant.io/v1alpha1` | Per-tenant token-based quotas |

### Gateway API Inference Extension CRDs

| CRD | Purpose |
|---|---|
| `InferencePool` | Defines the pool of vLLM backends + EPP |

### llm-d CRDs

| CRD | Purpose |
|---|---|
| `ModelService` (via Helm) | Deploys vLLM worker StatefulSets on GPU nodes |

---

## Tenant Management

Tenants are **Kubernetes Secrets** with standardized labels and annotations:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tenant-acme-api-key
  namespace: kuadrant-system
  labels:
    authorino.kuadrant.io/managed-by: authorino
    app: token-labs
  annotations:
    kuadrant.io/groups: "pro"           # tier: free | pro | enterprise
    secret.kuadrant.io/user-id: "acme"  # unique tenant identifier
stringData:
  api_key: "tlabs_sk_acme_..."          # API key value
type: Opaque
```

### Tenant Operations

| Operation | Method |
|---|---|
| Add tenant | `kubectl apply -f tenant-secret.yaml` |
| Remove tenant | `kubectl delete secret tenant-acme-api-key -n kuadrant-system` |
| Change tier | Update `kuadrant.io/groups` annotation |
| Rotate API key | Update `stringData.api_key` field |
| List tenants | `kubectl get secrets -n kuadrant-system -l app=token-labs` |

### Tier Quotas

| Tier | Token Limit | Request Limit | Window |
|---|---|---|---|
| `free` | 50,000 tokens | 100 requests | per day |
| `pro` | 500,000 tokens | 5,000 requests | per day |
| `enterprise` | 5,000,000 tokens | 50,000 requests | per day |

---

## Component Versions

| Component | Version | Chart |
|---|---|---|
| Envoy Gateway | v1.6.4 | `oci://docker.io/envoyproxy/gateway-helm` |
| Envoy AI Gateway | v0.5.0 | `oci://docker.io/envoyproxy/ai-gateway-helm` |
| Kuadrant Operator | latest | `kuadrant-operator` (Helm or OLM) |
| llm-d | v0.5.0 | 5-release helmfile |
| llm-d-infra | v1.3.6 | InferencePool CRDs + Gateway |
| InferencePool (Llama) | v1.3.0 | EPP for Llama pool |
| InferencePool (Nemotron VL) | v1.3.0 | EPP for Nemotron VL pool |
| llm-d-modelservice (Llama) | v0.4.5 | vLLM Llama 3.1 8B on spark-01 |
| llm-d-modelservice (Nemotron VL) | v0.4.5 | vLLM Nemotron VL 12B FP8 on spark-02 |
| vLLM image | v0.5.0 | `ghcr.io/llm-d/llm-d-cuda:v0.5.0` |
| Magpie TTS | 357M | Custom container on spark-01 (GPU) |
| Gateway API Inference Extension | v1.3.0 | CRD manifests |

### Models Served

| Model | Type | Node | Pool | GPU Memory |
|---|---|---|---|---|
| `meta-llama/Llama-3.1-8B-Instruct` | Text LLM | spark-01 | `token-labs-pool` | ~16 GB |
| `nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8` | Vision-Language | spark-02 | `nemotron-vl-pool` | ~13 GB |
| `nvidia/magpie_tts_multilingual_357m` | Text-to-Speech | spark-01 | N/A (K8s Service) | ~700 MB |

---

## Deployment Order

```
1. Gateway API CRDs + Inference Extension CRDs + AI Gateway CRDs
2. Envoy Gateway + AI Gateway controller (+ Redis for rate limiting)
3. Kuadrant Operator (deploys Authorino + Limitador)
4. llm-d helmfile (infra + 2× modelservice + 2× inferencepool)
5. AIGatewayRoute (model-based routing to InferencePool backends)
6. Gateway (LLM + TTS listeners)
7. Magpie TTS deployment (GPU, spark-01)
8. Kuadrant policies (AuthPolicy, RateLimitPolicy, TokenRateLimitPolicy)
9. Tenant secrets
```

---

## Architecture Diagram

```
                          ┌─────────────────────────────────────────┐
                          │           Kubernetes Cluster             │
                          │                                         │
  ┌──────────┐           │  ┌─────────────────────────────────────┐│
  │  Client   │──────────┼─►│  Gateway (Envoy Gateway)            ││
  │ (API key) │           │  │  gatewayClassName: eg               ││
  └──────────┘           │  │                                     ││
                          │  │  ┌────────────┐  ┌───────────────┐ ││
                          │  │  │ AuthPolicy │  │ RateLimitPolicy│ ││
                          │  │  │ (Authorino)│  │ (Limitador)    │ ││
                          │  │  └─────┬──────┘  └──────┬────────┘ ││
                          │  │        │                │          ││
                          │  │  ┌─────▼────────────────▼────────┐ ││
                          │  │  │  ext_proc (AI Gateway)        │ ││
                          │  │  │  Envoy AI Gateway controller   │ ││
                          │  │  │  Reads "model" → sets header   │ ││
                          │  │  │  x-ai-eg-model                │ ││
                          │  │  └──────────────┬────────────────┘ ││
                          │  │                 │                  ││
                          │  │  ┌──────────────▼────────────────┐ ││
                          │  │  │  AIGatewayRoute               │ ││
                          │  │  │  header → InferencePool       │ ││
                          │  │  └──────────────┬────────────────┘ ││
                          │  │                 │                  ││
                          │  │  ┌──────────────▼────────────────┐ ││
                          │  │  │  ext_proc (llm-d EPP)         │ ││
                          │  │  │  Inference Scheduling          │ ││
                          │  │  └──────────────┬────────────────┘ ││
                          │  │                 │                  ││
                          │  │  ┌──────────────▼────────────────┐ ││
                          │  │  │  TokenRateLimitPolicy          │ ││
                          │  │  │  (Limitador — token quotas)    │ ││
                          │  │  └───────────────────────────────┘ ││
                          │  └─────────────────────────────────────┘│
                          │                                         │
                          │    ┌──────────────┐  ┌──────────────┐   │
                          │    │ token-labs-   │  │ nemotron-vl- │   │
                          │    │ pool (Llama)  │  │ pool (VL)    │   │
                          │    │ ┌──────────┐  │  │ ┌──────────┐ │   │
                          │    │ │ vLLM     │  │  │ │ vLLM     │ │   │
                          │    │ │ spark-01 │  │  │ │ spark-02 │ │   │
                          │    │ │ Llama 8B │  │  │ │Nemotron  │ │   │
                          │    │ └──────────┘  │  │ │VL 12B FP8│ │   │
                          │    └──────────────┘  │ └──────────┘ │   │
                          │                      └──────────────┘   │
                          │                                         │
                          │    ┌──────────────────────────────┐     │
                          │    │  Magpie TTS (spark-01/GPU)    │     │
                          │    │  /v1/audio/speech             │     │
                          │    │  357M params, NeMo            │     │
                          │    └──────────────────────────────┘     │
                          └─────────────────────────────────────────┘
```

---

## Key Design Decisions

1. **No custom application code** — All multi-tenancy is implemented via Kubernetes CRDs (Kuadrant AuthPolicy, RateLimitPolicy, TokenRateLimitPolicy). Tenant onboarding is a `kubectl apply` of a Secret.

2. **Envoy Gateway over Istio** — The Gateway API Inference Extension explicitly supports Envoy Gateway. It's lighter weight than a full Istio mesh and provides native `SecurityPolicy` and `BackendTrafficPolicy` CRDs.

3. **Kuadrant over custom rate limiting** — Kuadrant's `TokenRateLimitPolicy` automatically extracts `usage.total_tokens` from OpenAI-compatible responses, providing token-based billing/quotas without any custom middleware.

4. **llm-d for inference routing** — llm-d's EPP provides KV-cache-aware and queue-depth-aware routing that generic load balancers cannot match, critical for LLM serving performance.

5. **Separation of concerns** — Each project handles exactly one domain:
   - Envoy Gateway = proxy/networking
   - Kuadrant = auth + rate limiting (tenant controls)
   - llm-d = inference-aware scheduling

6. **Multi-model routing via Envoy AI Gateway** — Each LLM model gets its own InferencePool + EPP. The AI Gateway controller runs as an ext_proc extension, reads the `"model"` field from the request body, and sets the `x-ai-eg-model` header. The `AIGatewayRoute` CRD matches on this header to route to the correct InferencePool backend. Token usage is tracked via `llmRequestCosts` (InputToken, OutputToken, TotalToken) for per-request cost accounting.

7. **TTS as a separate service** — Magpie TTS uses NeMo (not vLLM), so it runs as a standalone FastAPI service behind the same Gateway. It runs on GPU on spark-01 (shared with Llama) for faster inference. It shares the same Kuadrant auth/rate-limiting policies via its own HTTPRoute.

8. **GPU allocation: Llama + TTS on spark-01, Nemotron VL on spark-02** — Llama 3.1 8B runs at 80% GPU utilization on spark-01, leaving room for Magpie TTS (~700 MB). Nemotron VL 12B FP8 (~13 GB) uses spark-02 at 90% utilization.

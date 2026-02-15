# Token Labs

[![Deploy and Benchmark](https://github.com/elizabetht/token-labs/actions/workflows/deploy-and-benchmark.yml/badge.svg)](https://github.com/elizabetht/token-labs/actions/workflows/deploy-and-benchmark.yml)
[![Build vLLM](https://github.com/elizabetht/token-labs/actions/workflows/build-and-push.yml/badge.svg?event=push)](https://github.com/elizabetht/token-labs/actions/workflows/build-and-push.yml)
[![Latest Release](https://img.shields.io/github/v/tag/elizabetht/token-labs?label=Latest%20Release)](https://github.com/elizabetht/token-labs/releases)

Multi-tenant LLM inference-as-a-service on NVIDIA DGX Spark. All tenant management, authentication, rate limiting, and inference routing is implemented via Kubernetes CRDs — zero custom application code.

## Architecture

TokenLabs composes three open-source projects, each handling a distinct concern:

```
Client (Authorization: Bearer <api-key>)
  │
  ▼
┌──────────────────────────────────────────────────────────────┐
│  Envoy Gateway  (gatewayClassName: eg)                       │
│  ├─ Kuadrant AuthPolicy → Authorino        ① API key auth   │
│  ├─ Kuadrant RateLimitPolicy → Limitador   ② Rate limits    │
│  │                                                           │
│  ├─ AI Gateway ext_proc                   ③ Model routing  │
│  │   reads "model" → sets x-ai-eg-model header              │
│  ├─ AIGatewayRoute → InferencePool                           │
│  │   ├─ model=Llama-3.1-8B     → token-labs-pool (spark-01) │
│  │   └─ model=Nemotron-VL-12B  → nemotron-vl-pool (spark-02)│
│  ├─ llm-d EPP ext_proc                    ④ Inference sched │
│  │                                                           │
│  └─ /v1/audio/speech ──► Magpie TTS        ⑤ Text-to-speech │
├──────────────────────────────────────────────────────────────┤
│  vLLM / TTS Workers                                          │
│  └─ Response with usage.total_tokens (LLMs)                  │
├──────────────────────────────────────────────────────────────┤
│  Kuadrant TokenRateLimitPolicy → Limitador  ⑥ Token quota   │
└──────────────────────────────────────────────────────────────┘
  │
  ▼
Client receives response
```

### Components

**[Envoy Gateway](https://gateway.envoyproxy.io/)** — Kubernetes-native L7 proxy that implements the Gateway API. It serves as the single entry point for all client traffic. Envoy Gateway handles TLS termination, HTTP routing, and hosts ext_proc filters for the AI Gateway controller and llm-d EPP. It was chosen over Istio because the Gateway API Inference Extension explicitly supports it, and it's lighter weight than a full service mesh.

**[Envoy AI Gateway](https://aigateway.envoyproxy.io/)** — AI-native routing layer that runs on top of Envoy Gateway. Its controller runs as an ext_proc extension that automatically extracts the `"model"` field from the request body, sets the `x-ai-eg-model` header, and routes to the correct InferencePool backend via `AIGatewayRoute` rules. It also tracks per-request token usage via `llmRequestCosts` (InputToken, OutputToken, TotalToken). This replaces the need for a separate Body Based Router or custom HTTPRoute header matching.

**[Kuadrant](https://docs.kuadrant.io/)** — CNCF policy layer that deploys two backing services:
- **Authorino** — external authorization service. When the `AuthPolicy` CRD is applied, Authorino intercepts every request and validates the API key (stored as a Kubernetes Secret). It extracts tenant metadata (tier, user-id) from the Secret's annotations and enriches the request context so downstream policies can use it.
- **Limitador** — rate limiting service. Enforces request-count limits (via `RateLimitPolicy`) and, critically, token-based quotas (via `TokenRateLimitPolicy`). The token policy automatically parses `usage.total_tokens` from OpenAI-compatible JSON responses and counts it against the tenant's quota — no custom middleware required. This is what makes per-tenant billing feasible without writing a proxy.

**[llm-d](https://llm-d.ai/)** — inference-aware request scheduler. Its Endpoint Picker (EPP) runs as an Envoy `ext_proc` server and scores every vLLM pod on three signals before routing the request:
1. **KV-cache usage** — avoids pods whose GPU memory is nearly full
2. **Prefix-cache locality** — routes similar prompts to the same pod to reuse cached KV entries
3. **Queue depth** — prefers pods with fewer in-flight requests

This produces better tail latency and higher throughput than round-robin or least-connections load balancing.

**[vLLM](https://github.com/vllm-project/vllm)** — high-performance LLM inference engine running on DGX Spark GB10 GPUs. Served via the `ghcr.io/llm-d/llm-d-cuda:v0.5.0` container image. Exposes an OpenAI-compatible API (`/v1/chat/completions`, `/v1/completions`, `/v1/models`). Currently serves two models:
- **Llama 3.1 8B Instruct** (spark-01) — general-purpose chat model
- **Nemotron VL 12B FP8** (spark-02) — NVIDIA vision-language model with FP8 quantization, supports image+text inputs

**[Magpie TTS](https://huggingface.co/nvidia/magpie_tts_multilingual_357m)** — NVIDIA's multilingual text-to-speech model (357M parameters). Runs on spark-01 (GPU, shared with Llama 3.1 8B). Served via a custom FastAPI wrapper that exposes an OpenAI-compatible `/v1/audio/speech` endpoint. Supports 5 voices and 7 languages (en, es, de, fr, vi, it, zh). Built on the NeMo framework.

### Infrastructure

```
┌──────────────────────────────────────────────────────────────────────┐
│                         MicroK8s Cluster                             │
│                                                                      │
│  ┌────────────────┐   ┌────────────────┐   ┌────────────────┐       │
│  │  controller     │   │  spark-01      │   │  spark-02      │       │
│  │  (CPU, ARM64)   │   │  (GB10 GPU)    │   │  (GB10 GPU)    │       │
│  │                 │   │                │   │                │       │
│  │  Envoy GW       │   │  vLLM:         │   │  vLLM:         │       │
│  │  Kuadrant       │   │  Llama 3.1 8B  │   │  Nemotron VL   │       │
│  │  llm-d EPPs     │   │  Magpie TTS    │   │  12B FP8       │       │
│  └────────────────┘   └────────────────┘   └────────────────┘       │
└──────────────────────────────────────────────────────────────────────┘
```

The cluster has three nodes. The CPU controller runs control-plane components (Envoy Gateway proxy, Kuadrant operators, llm-d EPPs). **spark-01** serves `meta-llama/Llama-3.1-8B-Instruct` (80% GPU utilization) and Magpie TTS (GPU-accelerated, ~700 MB). **spark-02** serves `nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8` (FP8 quantized vision-language model). Both use `tensor_parallelism=1`.

### Tenant Model

There is no external identity provider (no Keycloak, no Auth0). Tenants are Kubernetes Secrets — Authorino validates API keys by looking up Secrets directly. No database, no restarts, no config reloads. The moment you `kubectl apply` a tenant Secret, the API key is live.

#### How authentication works

1. Client sends a request with `Authorization: Bearer <api-key>`
2. Authorino searches for a Secret in `kuadrant-system` labeled `authorino.kuadrant.io/managed-by: authorino`
3. Compares the `api_key` field in each Secret against the bearer token
4. On match, extracts the tenant's tier (`kuadrant.io/groups`) and ID (`secret.kuadrant.io/user-id`) from annotations
5. Passes this metadata downstream — RateLimitPolicy and TokenRateLimitPolicy use it to enforce per-tenant quotas

#### Tenant Secret structure

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tenant-acme
  namespace: kuadrant-system
  labels:
    authorino.kuadrant.io/managed-by: authorino   # Authorino discovers this Secret
    app: token-labs
  annotations:
    kuadrant.io/groups: "pro"                      # tier: free | pro | enterprise
    secret.kuadrant.io/user-id: "acme"             # unique tenant ID (rate limit counter key)
stringData:
  api_key: "tlabs_sk_acme_..."                     # API key value
```

#### Onboarding a new tenant

```bash
# 1. Generate a secure API key
API_KEY="tlabs_sk_$(openssl rand -hex 24)"

# 2. Create the tenant Secret (choose tier: free, pro, or enterprise)
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: tenant-acme
  namespace: kuadrant-system
  labels:
    authorino.kuadrant.io/managed-by: authorino
    app: token-labs
  annotations:
    kuadrant.io/groups: "pro"
    secret.kuadrant.io/user-id: "acme"
stringData:
  api_key: "$API_KEY"
EOF

# 3. Share the API key with the client (securely, out-of-band)
echo "API Key: $API_KEY"
```

The client can use the key immediately — no waiting, no restart:

```bash
curl https://inference.token-labs.local/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "meta-llama/Llama-3.1-8B-Instruct", "messages": [{"role": "user", "content": "Hello"}]}'
```

#### Managing tenants

| Action | Command |
|--------|---------|
| List all tenants | `kubectl get secrets -n kuadrant-system -l app=token-labs` |
| Change tier | `kubectl annotate secret tenant-acme -n kuadrant-system kuadrant.io/groups=enterprise --overwrite` |
| Rotate API key | `kubectl create secret generic tenant-acme -n kuadrant-system --from-literal=api_key="$(openssl rand -hex 24)" --dry-run=client -o yaml \| kubectl apply -f -` |
| Revoke access | `kubectl delete secret tenant-acme -n kuadrant-system` |

#### Rate limits by tier

| Tier | Requests/day | Requests/min | Tokens/day | Tokens/min |
|------|-------------|-------------|-----------|-----------|
| Free | 100 | 10 | 50,000 | 5,000 |
| Pro | 5,000 | 100 | 500,000 | 50,000 |
| Enterprise | 50,000 | 1,000 | 5,000,000 | 500,000 |

> See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full CRD inventory, request flow details, and design decisions.

---

## Deployment Guide

### Prerequisites

- MicroK8s cluster with GPU addon enabled on worker nodes
- `kubectl` v1.28+ configured for the cluster
- `helm` v3.12+
- `helmfile` v1.1+
- HuggingFace token with access to `meta-llama/Llama-3.1-8B-Instruct` and `nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8`

#### MicroK8s CLI aliases

All scripts and commands in this guide use standard `kubectl` and `helm`. On a MicroK8s cluster, create aliases so they resolve to the MicroK8s-bundled binaries:

```bash
# Permanent system-wide aliases (recommended)
sudo snap alias microk8s.kubectl kubectl
sudo snap alias microk8s.helm helm

# Or add to ~/.bashrc / ~/.zshrc
echo 'alias kubectl="microk8s kubectl"' >> ~/.zshrc
echo 'alias helm="microk8s helm"' >> ~/.zshrc
source ~/.zshrc
```

Verify:
```bash
kubectl version --client
helm version
```

### Step 1: Install Gateway API CRDs

This installs the [Gateway API](https://gateway-api.sigs.k8s.io/) base CRDs (Gateway, HTTPRoute, GatewayClass), the [Gateway API Inference Extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension) CRDs (InferencePool), and the [Envoy AI Gateway](https://aigateway.envoyproxy.io/) CRDs (AIGatewayRoute). These are the Kubernetes resource definitions that all projects build upon.

```bash
./deploy/scripts/01-install-crds.sh
```

What it does:
- Applies Gateway API v1.4.1 standard CRDs
- Applies Inference Extension v1.3.0 CRDs (graduated InferencePool at `inference.networking.k8s.io/v1`)
- Installs Envoy AI Gateway v0.5.0 CRDs (AIGatewayRoute)

Verify:
```bash
kubectl get crd gateways.gateway.networking.k8s.io
kubectl get crd inferencepools.inference.networking.k8s.io
kubectl get crd aigatewayroutes.aigateway.envoyproxy.io
```

### Step 2: Install Envoy Gateway + AI Gateway + Redis

[Envoy Gateway](https://gateway.envoyproxy.io/) is the data-plane proxy. [Envoy AI Gateway](https://aigateway.envoyproxy.io/) adds AI-native routing on top — its controller extracts the model from the request body and routes to the correct InferencePool. Redis is required as the backend for Kuadrant's distributed rate limiting (Limitador stores counters in Redis).

```bash
./deploy/scripts/02-install-envoy-gateway.sh
```

What it does:
1. Deploys a standalone Redis instance into `redis-system`
2. Installs Envoy Gateway v1.6.4 Helm chart with AI Gateway values files (extension manager, rate limiting addon, InferencePool addon)
3. Installs the AI Gateway controller v0.5.0 into `envoy-ai-gateway-system`

Verify:
```bash
kubectl get pods -n envoy-gateway-system       # envoy-gateway controller running
kubectl get pods -n envoy-ai-gateway-system    # ai-gateway controller running
kubectl get pods -n redis-system               # redis pod running
```

### Step 3: Install Kuadrant

[Kuadrant](https://docs.kuadrant.io/) is the policy layer. Installing the operator deploys the controller that watches for `AuthPolicy`, `RateLimitPolicy`, and `TokenRateLimitPolicy` CRDs. Creating the `Kuadrant` CR bootstraps the backing services (Authorino for auth, Limitador for rate limiting).

```bash
./deploy/scripts/03-install-kuadrant.sh
```

What it does:
1. Adds the Kuadrant Helm repo and installs `kuadrant-operator` into `kuadrant-system`
2. Creates a `Kuadrant` CR that triggers deployment of Authorino and Limitador

Verify:
```bash
kubectl get pods -n kuadrant-system   # operator, authorino, limitador all running
kubectl get kuadrant -n kuadrant-system  # status should show Ready
```

### Step 4: Deploy llm-d (inference stack)

[llm-d](https://llm-d.ai/) is the inference-aware scheduling layer. It uses a 5-release Helmfile pattern:

| Chart | Release | What it deploys |
|-------|---------|----------------|
| `llm-d-infra` v1.3.6 | `llm-d-infra` | CRDs and shared infrastructure. Gateway creation is disabled (`gateway.create: false`) since we manage the Gateway resource separately via Envoy Gateway. |
| `inferencepool` v1.3.0 | `llm-d-inferencepool` | EPP for Llama 3.1 8B — the ext_proc server that performs inference-aware routing with `kvCacheAware` and `queueDepthAware` scoring. |
| `inferencepool` v1.3.0 | `llm-d-inferencepool-nemotron-vl` | EPP for Nemotron VL 12B — separate EPP instance for the vision-language model pool. |
| `llm-d-modelservice` v0.4.5 | `llm-d-modelservice` | vLLM worker for Llama 3.1 8B Instruct. 1 decode replica on spark-01. |
| `llm-d-modelservice` v0.4.5 | `llm-d-modelservice-nemotron-vl` | vLLM worker for Nemotron VL 12B FP8. 1 decode replica on spark-02 with `--trust-remote-code --quantization=modelopt`. |

```bash
# Set your HuggingFace token first
kubectl create namespace token-labs
kubectl create secret generic hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  -n token-labs

./deploy/scripts/04-deploy-llm-d.sh
```

What it does:
1. Creates the `token-labs` namespace
2. Runs `helmfile apply` which installs all 5 releases with values from `deploy/llm-d/values/`
3. Waits for vLLM workers to download model weights and become ready (can take several minutes on first run)

Verify:
```bash
kubectl get pods -n token-labs           # 2 vLLM pods + 2 EPP pods running
kubectl get inferencepool -n token-labs  # both pools should show Ready
```

### Step 5: Deploy AI Gateway Route

The [Envoy AI Gateway](https://aigateway.envoyproxy.io/) uses the `AIGatewayRoute` CRD for model-based routing. The AI Gateway controller automatically extracts the `"model"` field from the request body, sets the `x-ai-eg-model` header, and the `AIGatewayRoute` matches on this header to route to the correct InferencePool backend. Token usage is tracked via `llmRequestCosts`.

```bash
./deploy/scripts/05-deploy-ai-gateway-route.sh
```

What it does:
1. Applies the `AIGatewayRoute` resource which defines model-to-InferencePool routing rules
2. Configures `llmRequestCosts` for per-request token tracking (InputToken, OutputToken, TotalToken)

Verify:
```bash
kubectl get aigatewayroute -n token-labs    # AIGatewayRoute listed
```

### Step 6: Deploy Magpie TTS

Magpie TTS is deployed as a standalone service (not through llm-d) because it uses the NeMo framework, not vLLM:

```bash
kubectl apply -f deploy/magpie-tts/
```

What it deploys:
- A Deployment running the FastAPI TTS wrapper on spark-01 (GPU-accelerated, shared with Llama)
- A Service exposing port 8000
- An HTTPRoute mapping `/v1/audio/speech` through the same Gateway

Verify:
```bash
kubectl get pods -n token-labs -l app=magpie-tts  # 1 pod running
kubectl get httproute -n token-labs               # magpie-tts-route listed
```

### Step 7: Apply Gateway, routes, and policies

This step creates the actual networking and policy resources that wire everything together:

```bash
# Gateway + HTTPRoute
kubectl apply -f deploy/gateway/

# Kuadrant policies
kubectl apply -f deploy/policies/
```

**Gateway resources** (`deploy/gateway/`):
- `namespace.yaml` — creates the `token-labs` namespace (idempotent)
- `gateway.yaml` — creates a `Gateway` resource with `gatewayClassName: eg`, listening on HTTP port 80 with hostname `inference.token-labs.local`. Envoy Gateway sees this and provisions an Envoy proxy pod to handle traffic.
- `aigatewayroute.yaml` — creates an `AIGatewayRoute` with per-model header matching (deployed in step 5). The AI Gateway controller extracts the `"model"` field from the request body and sets the `x-ai-eg-model` header. Each rule matches on this header and routes to the correct `InferencePool` backend. The InferencePool is the bridge to llm-d's EPP — when Envoy receives a matching request, it invokes the EPP via ext_proc to pick the optimal vLLM pod.

**Kuadrant policies** (`deploy/policies/`):
- `kuadrant.yaml` — the `Kuadrant` CR (idempotent, already created in step 3)
- `auth-policy.yaml` — `AuthPolicy` targeting the Gateway. Configures API key authentication: Authorino validates the `Authorization: Bearer <key>` header by looking up Secrets labeled `authorino.kuadrant.io/managed-by: authorino`. On match, it extracts `kuadrant.io/groups` (tier) and `secret.kuadrant.io/user-id` (tenant ID) from annotations and passes them in the request context. An OPA policy validates the tier is one of `free`, `pro`, or `enterprise`.
- `rate-limit-policy.yaml` — `RateLimitPolicy` targeting the Gateway. Defines per-tier request count limits (e.g., free = 10/min and 100/day). Uses `when` predicates with CEL expressions to match `auth.identity.groups` and `counters` keyed by `auth.identity.userid` for tenant isolation.
- `token-rate-limit-policy.yaml` — `TokenRateLimitPolicy` targeting the Gateway for `/v1/chat/completions`. This is the key CRD for LLM billing. After vLLM returns a response, Kuadrant's wasm-shim parses `usage.total_tokens` from the JSON body and sends it to Limitador as `hits_addend`. Each tenant's cumulative token usage is tracked per time window.

Verify:
```bash
kubectl get gateway -n token-labs            # Programmed: True
kubectl get aigatewayroute -n token-labs     # Listed
kubectl get authpolicy -n token-labs         # Accepted: True
kubectl get ratelimitpolicy -n token-labs    # Accepted: True
kubectl get tokenratelimitpolicy -n token-labs  # Accepted: True
```

### Step 8: Create tenant API keys

Demo tenants are provided for testing (see [Tenant Model](#tenant-model) above for full onboarding instructions):

```bash
kubectl apply -f deploy/tenants/
```

This creates two demo tenants:
- `tenant-free-demo` — free tier, key `tlabs_free_demo_key_change_me`
- `tenant-pro-demo` — pro tier, key `tlabs_pro_demo_key_change_me`

For production tenants, follow the [onboarding steps](#onboarding-a-new-tenant) in the Tenant Model section.

### Test it

```bash
# Port-forward to the gateway (or use MetalLB IP)
kubectl port-forward -n envoy-gateway-system \
  svc/envoy-default-token-labs-gateway 8080:80 &

# List models
curl -s http://localhost:8080/v1/models \
  -H "Authorization: Bearer tlabs_pro_demo_key_change_me" | jq

# Chat completion (Llama 3.1 8B)
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer tlabs_pro_demo_key_change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "What is Kubernetes?"}],
    "max_tokens": 200
  }' | jq

# Vision-language (Nemotron VL 12B)
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer tlabs_pro_demo_key_change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8",
    "messages": [{"role": "user", "content": "Describe this image."}],
    "max_tokens": 200
  }' | jq

# Text-to-speech (Magpie TTS)
curl -s http://localhost:8080/v1/audio/speech \
  -H "Authorization: Bearer tlabs_pro_demo_key_change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Welcome to Token Labs.",
    "voice": "aria",
    "language": "en"
  }' --output speech.wav

# Verify rate limiting (free tier — should get 429 after 10 requests/min)
for i in $(seq 1 15); do
  echo -n "Request $i: "
  curl -s -o /dev/null -w "%{http_code}" \
    http://localhost:8080/v1/chat/completions \
    -H "Authorization: Bearer tlabs_free_demo_key_change_me" \
    -H "Content-Type: application/json" \
    -d '{"model":"meta-llama/Llama-3.1-8B-Instruct","messages":[{"role":"user","content":"Hi"}],"max_tokens":5}'
  echo
done
```

---

## Observability

The stack exposes metrics from all layers via Prometheus ServiceMonitors:

```bash
# Optional: deploy ServiceMonitors
kubectl apply -f deploy/monitoring/service-monitors.yaml
```

| Source | Key Metrics |
|--------|-------------|
| Limitador | `limitador_counter_hits_total` — per-tenant request/token counts |
| Authorino | `auth_server_response_status` — auth allow/deny rates |
| vLLM | `vllm:kv_cache_usage_perc`, `vllm:request_latency_seconds` |
| EPP | Routing decisions, prefix-cache hit rates |

llm-d also provides ready-made Grafana dashboards — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

---

## Benchmarks

| Metric | Prefill (Input) | Decode (Output) |
|--------|-----------------|-----------------|
| Throughput | 3,203 tok/s | 520 tok/s |
| Cost/1M tokens | $0.006 | $0.037 |

- [Full Benchmark Results](https://elizabetht.github.io/token-labs/benchmark-results.html)
- [Raw JSON Data](https://elizabetht.github.io/token-labs/benchmark-results.json)

## Accuracy Testing

Uses [lighteval](https://github.com/huggingface/lighteval) with the IFEval benchmark to verify model quality across quantizations. Models are compared against the `meta-llama/Llama-3.1-8B-Instruct` baseline using a ±5% threshold. See [baselines/README.md](baselines/README.md) for details.

## Repository Structure

```
├── deploy/
│   ├── scripts/              # Installation scripts (run in order)
│   │   ├── 01-install-crds.sh
│   │   ├── 02-install-envoy-gateway.sh
│   │   ├── 03-install-kuadrant.sh
│   │   ├── 04-deploy-llm-d.sh
│   │   └── 05-deploy-ai-gateway-route.sh
│   ├── gateway/              # Gateway + AIGatewayRoute resources
│   ├── llm-d/                # Helmfile + values for llm-d 5-release deploy
│   │   ├── helmfile.yaml.gotmpl
│   │   └── values/
│   ├── magpie-tts/           # Magpie TTS deployment + HTTPRoute
│   ├── policies/             # Kuadrant AuthPolicy, RateLimitPolicy, TokenRateLimitPolicy
│   ├── tenants/              # Tenant API key Secrets (template + demos)
│   └── monitoring/           # Prometheus ServiceMonitors
├── services/
│   └── magpie-tts/           # FastAPI TTS wrapper (server.py, Dockerfile)
├── docs/
│   ├── ARCHITECTURE.md       # Full architecture deep-dive
│   ├── index.html            # Live demo page
│   └── benchmark-results.*   # Benchmark data
├── baselines/                # Accuracy baseline values
├── scripts/                  # Benchmark and analysis scripts
├── Dockerfile                # vLLM build for ARM64
└── .github/workflows/        # CI/CD pipelines
```

## Links

- [Live Demo](https://elizabetht.github.io/token-labs/)
- [Architecture Deep-Dive](docs/ARCHITECTURE.md)
- [Benchmark Results](https://elizabetht.github.io/token-labs/benchmark-results.html)

## License

MIT

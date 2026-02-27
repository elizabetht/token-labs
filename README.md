# Token Labs

[![Deploy and Benchmark](https://github.com/elizabetht/token-labs/actions/workflows/deploy-and-benchmark.yml/badge.svg)](https://github.com/elizabetht/token-labs/actions/workflows/deploy-and-benchmark.yml)
[![Build vLLM](https://github.com/elizabetht/token-labs/actions/workflows/build-and-push.yml/badge.svg?event=push)](https://github.com/elizabetht/token-labs/actions/workflows/build-and-push.yml)
[![Latest Release](https://img.shields.io/github/v/tag/elizabetht/token-labs?label=Latest%20Release)](https://github.com/elizabetht/token-labs/releases)

Multi-tenant LLM inference-as-a-service on NVIDIA DGX Spark. All tenant management, authentication, rate limiting, and inference routing is implemented via Kubernetes CRDs — zero custom application code.

## Architecture

TokenLabs composes four open-source projects, each handling a distinct concern:

```
Client (Authorization: Bearer <api-key>)
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Envoy AI Gateway (EAG v0.5.0) + Envoy Gateway (EG v1.5.0)      │
│  ├─ Kuadrant AuthPolicy → Authorino          ① API key auth      │
│  ├─ Kuadrant RateLimitPolicy → Limitador     ② Rate limits       │
│  │                                                               │
│  ├─ AIGatewayRoute: /v1/chat/completions     ③ Model routing     │
│  │   EAG reads {"model":...} body → x-ai-eg-model header        │
│  │   ├─ model=Nemotron-Llama-8B  → llm-d EPP → vLLM (spark-01) │
│  │   └─ model=Nemotron-VL-12B   → llm-d EPP → vLLM (spark-02) │
│  │                                                               │
│  ├─ HTTPRoute: /v1/audio/speech ──► Magpie TTS  ④ Text-to-speech│
│  └─ HTTPRoute: /v1/audio/transcriptions ──► Riva STT (NVIDIA NIM)│
├──────────────────────────────────────────────────────────────────┤
│  vLLM / TTS Workers                                              │
│  └─ Response with usage.total_tokens (LLMs)                      │
├──────────────────────────────────────────────────────────────────┤
│  Kuadrant TokenRateLimitPolicy → Limitador    ⑤ Token quota      │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
Client receives response
```

### Components

**[Envoy AI Gateway](https://aigateway.envoyproxy.io/) (EAG v0.5.0)** — AI-aware proxy layer that extends Envoy Gateway. EAG adds the `AIGatewayRoute` CRD which natively parses the `"model"` field from JSON request bodies, sets the `x-ai-eg-model` header, and routes to the matching `InferencePool`. It also introduces `BackendSecurityPolicy` for upstream credential injection (used by Riva STT to swap the client's token-labs key for the NVIDIA API key). EAG replaces the old Body Based Router (BBR) ext_proc pattern — no ConfigMaps, no extra sidecar.

**[Envoy Gateway](https://gateway.envoyproxy.io/) (EG v1.5.0)** — Kubernetes-native L7 proxy that implements the Gateway API. EG is the data plane; EAG extends it via an xDS extension manager hook. EG provisions Envoy proxy pods, handles TLS termination, and hosts ext_proc filters for llm-d's EPP. Chosen over Istio because the Gateway API Inference Extension explicitly supports it and it's lighter weight than a full service mesh.

**[Kuadrant](https://docs.kuadrant.io/)** — CNCF policy layer that deploys two backing services:
- **Authorino** — external authorization service. When the `AuthPolicy` CRD is applied, Authorino intercepts every request and validates the API key (stored as a Kubernetes Secret). It extracts tenant metadata (tier, user-id) from the Secret's annotations and enriches the request context so downstream policies can use it.
- **Limitador** — rate limiting service. Enforces request-count limits (via `RateLimitPolicy`) and, critically, token-based quotas (via `TokenRateLimitPolicy`). The token policy automatically parses `usage.total_tokens` from OpenAI-compatible JSON responses and counts it against the tenant's quota — no custom middleware required. This is what makes per-tenant billing feasible without writing a proxy.

**[llm-d](https://llm-d.ai/) (v0.5.0)** — inference-aware request scheduler. Its Endpoint Picker (EPP) runs as an Envoy `ext_proc` server and scores every vLLM pod on three signals before routing the request:
1. **KV-cache usage** — avoids pods whose GPU memory is nearly full
2. **Prefix-cache locality** — routes similar prompts to the same pod to reuse cached KV entries
3. **Queue depth** — prefers pods with fewer in-flight requests

This produces better tail latency and higher throughput than round-robin or least-connections load balancing.

**[vLLM](https://github.com/vllm-project/vllm)** — high-performance LLM inference engine running on DGX Spark GB10 GPUs. Served via the `ghcr.io/llm-d/llm-d-cuda:v0.5.0` container image. Exposes an OpenAI-compatible API (`/v1/chat/completions`, `/v1/completions`, `/v1/models`). Currently serves two models:
- **Nemotron-Llama 8B** (`nvidia/Llama-3.1-Nemotron-Nano-8B-v1`, spark-01) — general-purpose chat model, BF16, 80% GPU utilization
- **Nemotron VL 12B FP8** (`nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8`, spark-02) — NVIDIA vision-language model with FP8 quantization, supports image+text inputs

**[Magpie TTS](https://huggingface.co/nvidia/magpie_tts_multilingual_357m)** — NVIDIA's multilingual text-to-speech model (357M parameters). Runs on spark-01 in **CPU mode** (the GB10 GPU is fully allocated to the Nemotron-Llama vLLM pod). Served via a custom FastAPI wrapper that exposes an OpenAI-compatible `/v1/audio/speech` endpoint. Supports 5 voices and 7 languages (en, es, de, fr, vi, it, zh). Built on the NeMo framework.

**Riva STT (NVIDIA NIM proxy)** — speech-to-text via [NVIDIA NIM](https://docs.api.nvidia.com/nim/reference/riva-asr) at `integrate.api.nvidia.com`. TokenLabs proxies `/v1/audio/transcriptions` to the NIM endpoint using an Envoy Gateway `Backend` + `BackendTLSPolicy` (TLS toward NVIDIA) + EAG `BackendSecurityPolicy` (swaps the client's token-labs key for the NVIDIA API key). The client never sees the NVIDIA key.

### Infrastructure

```
┌──────────────────────────────────────────────────────────────────────┐
│                         MicroK8s Cluster                             │
│                                                                      │
│  ┌────────────────┐   ┌────────────────┐   ┌────────────────┐       │
│  │  controller     │   │  spark-01      │   │  spark-02      │       │
│  │  (CPU, ARM64)   │   │  (GB10 GPU)    │   │  (GB10 GPU)    │       │
│  │                 │   │                │   │                │       │
│  │  Envoy AI GW    │   │  vLLM:         │   │  vLLM:         │       │
│  │  Envoy GW       │   │  Nemotron-     │   │  Nemotron VL   │       │
│  │  Kuadrant       │   │  Llama 8B      │   │  12B FP8       │       │
│  │  llm-d EPPs     │   │  Magpie TTS    │   │                │       │
│  │                 │   │  (CPU mode)    │   │                │       │
│  └────────────────┘   └────────────────┘   └────────────────┘       │
└──────────────────────────────────────────────────────────────────────┘
```

The cluster has three nodes. The CPU controller runs control-plane components (Envoy AI Gateway, Envoy Gateway proxy, Kuadrant operators, llm-d EPPs). **spark-01** serves `nvidia/Llama-3.1-Nemotron-Nano-8B-v1` (80% GPU utilization, BF16) and Magpie TTS (CPU mode — the GPU is fully allocated to vLLM). **spark-02** serves `nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8` (FP8 quantized vision-language model, 90% GPU utilization). Both use `tensor_parallelism=1`.

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
  -d '{"model": "nvidia/Llama-3.1-Nemotron-Nano-8B-v1", "messages": [{"role": "user", "content": "Hello"}]}'
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

> **Quick reference:** If you've done this before and just need the commands, see [`deploy/README.md`](deploy/README.md).

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

### Step 2: Install Envoy AI Gateway + Envoy Gateway + Redis

[Envoy AI Gateway (EAG)](https://aigateway.envoyproxy.io/) extends Envoy Gateway with AI-specific routing. Install order matters: EAG CRDs first, then the EAG controller (which creates a TLS cert Secret), then EG configured to connect to EAG via its extension manager. Redis is required for Kuadrant's distributed rate limiting (Limitador stores counters in Redis).

```bash
./deploy/scripts/02-install-envoy-ai-gateway.sh
```

What it does:
1. Installs EAG CRDs (`ai-gateway-crds-helm` v0.5.0) — registers `AIGatewayRoute`, `AIServiceBackend`, `BackendSecurityPolicy`
2. Installs the EAG controller (`ai-gateway-helm` v0.5.0) into `envoy-ai-gateway-system` and waits for it to be ready (it creates the TLS cert Secret needed by EG)
3. Installs Envoy Gateway (`gateway-helm` v1.5.0) into `envoy-gateway-system` with EAG extension manager config and `InferencePool` as a valid backendRef type
4. Deploys a standalone Redis instance into `redis-system` for rate-limit counters

Verify:
```bash
kubectl get pods -n envoy-ai-gateway-system   # ai-gateway-controller running
kubectl get pods -n envoy-gateway-system       # envoy-gateway controller running
kubectl get pods -n redis-system               # redis pod running
kubectl get gatewayclass                       # "eg" class listed
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

### Step 5: Deploy Gateway, routes, and policies

This step creates the actual networking and policy resources that wire everything together:

```bash
# Gateway + AIGatewayRoute
kubectl apply -f deploy/gateway/namespace.yaml
kubectl apply -f deploy/gateway/gatewayclass.yaml
kubectl apply -f deploy/gateway/gateway.yaml
kubectl apply -f deploy/gateway/aigwroute.yaml

# Kuadrant policies
kubectl apply -f deploy/policies/
```

**Gateway resources** (`deploy/gateway/`):
- `namespace.yaml` — creates the `token-labs` namespace (idempotent)
- `gateway.yaml` — creates a `Gateway` resource with `gatewayClassName: eg`, listening on HTTP port 80 with hostname `inference.token-labs.local`. Envoy Gateway sees this and provisions an Envoy proxy pod to handle traffic.
- `aigwroute.yaml` — creates an `AIGatewayRoute` for `/v1/chat/completions`. EAG's AI filter reads the `"model"` field from the JSON request body and sets the `x-ai-eg-model` header. Each rule matches on this header and routes to the correct `InferencePool` backend. The `InferencePool` is the bridge to llm-d's EPP — Envoy invokes the EPP via ext_proc to pick the optimal vLLM pod for each request.

**Kuadrant policies** (`deploy/policies/`):
- `kuadrant.yaml` — the `Kuadrant` CR (idempotent, already created in step 3)
- `auth-policy.yaml` — `AuthPolicy` targeting the Gateway. Configures API key authentication: Authorino validates the `Authorization: Bearer <key>` header by looking up Secrets labeled `app: token-labs`. On match, it extracts `kuadrant.io/groups` (tier) and `secret.kuadrant.io/user-id` (tenant ID) from annotations and passes them in the request context. An OPA policy validates the tier is one of `free`, `pro`, or `enterprise`.
- `rate-limit-policy.yaml` — `RateLimitPolicy` targeting the Gateway. Defines per-tier request count limits (e.g., free = 10/min and 100/day). Uses `when` predicates with CEL expressions to match `auth.identity.groups` and `counters` keyed by `auth.identity.userid` for tenant isolation.
- `token-rate-limit-policy.yaml` — `TokenRateLimitPolicy` targeting the `AIGatewayRoute` for `/v1/chat/completions`. After vLLM returns a response, Kuadrant's wasm-shim parses `usage.total_tokens` from the JSON body and sends it to Limitador as `hits_addend`. Each tenant's cumulative token usage is tracked per time window.

Verify:
```bash
kubectl get gateway -n token-labs               # Programmed: True
kubectl get aigatewayroute -n token-labs        # llm-inference listed
kubectl get authpolicy -n token-labs            # Accepted: True
kubectl get ratelimitpolicy -n token-labs       # Accepted: True
kubectl get tokenratelimitpolicy -n token-labs  # Accepted: True
```

### Step 6: Create tenant API keys

Before applying the tenant manifests, set a real company name and generate a unique API key for each tenant. The files in `deploy/tenants/` use placeholder values that must be replaced.

**Using the template for a new tenant:**

```bash
COMPANY="acme"
TIER="pro"        # free | pro | enterprise
API_KEY="tlabs_sk_$(openssl rand -hex 24)"

sed \
  -e "s/COMPANY-NAME/${COMPANY}/g" \
  -e "s/\"pro\"/\"${TIER}\"/g" \
  -e "s/tlabs_CHANGEME/${API_KEY}/g" \
  deploy/tenants/tenant-template.yaml \
  | kubectl apply -f -

echo "Tenant: ${COMPANY}  Key: ${API_KEY}"
```

**Editing the demo tenant files before applying:**

Open `deploy/tenants/tenant-free-demo.yaml` and `tenant-pro-demo.yaml` and update:
- `metadata.name` — e.g. `tenant-acme-free`
- `secret.kuadrant.io/user-id` — unique identifier used as the rate-limit counter key
- `api_key` — replace the placeholder with a generated key:

```bash
# Generate keys for each tenant
echo "Free tier key:  tlabs_sk_$(openssl rand -hex 24)"
echo "Pro tier key:   tlabs_sk_$(openssl rand -hex 24)"
```

Then apply:

```bash
kubectl apply -f deploy/tenants/
```

Verify (keys are live immediately, no restart needed):

```bash
kubectl get secrets -n kuadrant-system -l app=token-labs

# Quick smoke test
GATEWAY_IP=$(kubectl get gateway token-labs-gateway -n token-labs \
  -o jsonpath='{.status.addresses[0].value}')
curl -s -o /dev/null -w "%{http_code}" \
  http://${GATEWAY_IP}/v1/models \
  -H "Host: inference.token-labs.local" \
  -H "Authorization: Bearer <your-key>"
# Expect: 200
```

---

## Optional Components

### Magpie TTS (text-to-speech)

Magpie TTS runs on spark-01 in CPU mode (the GB10 GPU is fully allocated to the Nemotron-Llama vLLM pod). It exposes an OpenAI-compatible `/v1/audio/speech` endpoint backed by `nvidia/magpie_tts_multilingual_357m`.

**Build the image** (must be built natively on spark-01 — see build notes in `deploy/README.md`):

```bash
# On spark-01
docker build -t ghcr.io/elizabetht/token-labs/magpie-tts:latest services/magpie-tts
docker push ghcr.io/elizabetht/token-labs/magpie-tts:latest
```

If the GHCR package is private, create a pull secret first:

```bash
kubectl create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username=elizabetht \
  --docker-password=<GITHUB_PAT_read:packages> \
  -n token-labs
```

**Deploy:**

```bash
kubectl apply -f deploy/magpie-tts/
```

The model (`nvidia/magpie_tts_multilingual_357m`) downloads from NGC on first pod startup — allow ~1–2 min.

Verify:

```bash
kubectl get pods -n token-labs -l app=magpie-tts   # 1 pod running
kubectl logs -n token-labs -l app=magpie-tts        # look for "model loaded successfully"
```

### Riva STT (speech-to-text via NVIDIA NIM)

Riva STT proxies `/v1/audio/transcriptions` to `integrate.api.nvidia.com`. Requires an NVIDIA API key.

```bash
kubectl create secret generic nvidia-nim-api-key \
  --from-literal=apiKey=nvapi-CHANGEME \
  -n token-labs

./deploy/scripts/05-deploy-services.sh
```

Verify:

```bash
kubectl get httproute -n token-labs                 # riva-stt listed
kubectl get backendsecuritypolicy -n token-labs     # nvidia-nim-api-key listed
```

### Test it

```bash
GATEWAY_IP=$(kubectl get gateway token-labs-gateway -n token-labs \
  -o jsonpath='{.status.addresses[0].value}')

# Chat completion — Nemotron-Llama 8B (spark-01)
curl -s http://${GATEWAY_IP}/v1/chat/completions \
  -H "Host: inference.token-labs.local" \
  -H "Authorization: Bearer <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/Llama-3.1-Nemotron-Nano-8B-v1",
    "messages": [{"role": "user", "content": "What is Kubernetes?"}],
    "max_tokens": 200
  }' | jq

# Vision-language — Nemotron VL 12B FP8 (spark-02)
curl -s http://${GATEWAY_IP}/v1/chat/completions \
  -H "Host: inference.token-labs.local" \
  -H "Authorization: Bearer <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8",
    "messages": [{"role": "user", "content": "Describe this image."}],
    "max_tokens": 200
  }' | jq

# Verify rate limiting (free tier — should get 429 after 10 requests/min)
for i in $(seq 1 15); do
  echo -n "Request $i: "
  curl -s -o /dev/null -w "%{http_code}" \
    http://${GATEWAY_IP}/v1/chat/completions \
    -H "Host: inference.token-labs.local" \
    -H "Authorization: Bearer <your-free-tier-key>" \
    -H "Content-Type: application/json" \
    -d '{"model":"nvidia/Llama-3.1-Nemotron-Nano-8B-v1","messages":[{"role":"user","content":"Hi"}],"max_tokens":5}'
  echo
done
```

**Optional — test audio services** (only after deploying Magpie TTS / Riva STT):

```bash
# Text-to-speech — Magpie TTS (spark-01, CPU mode)
curl -s http://${GATEWAY_IP}/v1/audio/speech \
  -H "Host: inference.token-labs.local" \
  -H "Authorization: Bearer <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"input": "Welcome to Token Labs.", "voice": "aria"}' \
  --output speech.wav

# Speech-to-text — Riva STT via NVIDIA NIM
curl -s http://${GATEWAY_IP}/v1/audio/transcriptions \
  -H "Host: inference.token-labs.local" \
  -H "Authorization: Bearer <your-api-key>" \
  -F "file=@audio.wav" \
  -F "model=nvidia/parakeet-ctc-1.1b"
```

---

## Observability

The stack exposes metrics from all layers via Prometheus ServiceMonitors:

```bash
# Optional: deploy ServiceMonitors (includes vLLM GPU metrics)
kubectl apply -f deploy/monitoring/service-monitors.yaml
```

| Source | Key Metrics |
|--------|-------------|
| Limitador | `limitador_counter_hits_total` — per-tenant request/token counts |
| Authorino | `auth_server_response_status` — auth allow/deny rates |
| vLLM | `vllm:num_requests_waiting`, `vllm:gpu_cache_usage_perc`, `vllm:avg_generation_throughput_toks_per_s` |
| EPP | Routing decisions, prefix-cache hit rates |

llm-d also provides ready-made Grafana dashboards — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

---

## GPU-Aware Autoscaling

vLLM workers scale horizontally based on GPU pressure signals rather than CPU/RAM. The HPA triggers scale-out before the queue becomes large, compensating for the 60–90 s model warm-up time.

### Setup

**1. Install Prometheus Adapter** (exposes vLLM metrics to the HPA API):

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace monitoring --create-namespace \
  --version 4.11.0 \
  -f deploy/autoscaling/prometheus-adapter-values.yaml
```

**2. Verify metrics are available** to the HPA:

```bash
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1" | jq '.resources[].name'
kubectl get --raw \
  "/apis/custom.metrics.k8s.io/v1beta1/namespaces/token-labs/pods/*/vllm_num_requests_waiting" \
  | jq '.items[].value'
```

**3. Apply the HPAs**:

```bash
# Confirm Deployment names match your cluster before applying
kubectl get deployments -n token-labs
kubectl apply -f deploy/autoscaling/hpa.yaml
```

**4. Watch scaling events**:

```bash
kubectl describe hpa -n token-labs
kubectl get events -n token-labs --field-selector reason=SuccessfulRescale
```

### Scale signals

| Metric | Threshold | Meaning |
|--------|-----------|---------|
| `vllm_num_requests_waiting` | > 5 per pod | Queue backing up — scale out before head-of-line blocking |
| `vllm_gpu_cache_usage_perc` | > 0.85 per pod | VRAM KV-cache near full — distribute load to avoid evictions |

### Cooldown configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `scaleUp.stabilizationWindowSeconds` | `0` | No delay on scale-out — model warm-up (60–90 s) means we must start immediately |
| `scaleUp.policies[].periodSeconds` | `90` | Add at most 1 pod per 90 s to match the model warm-up window |
| `scaleDown.stabilizationWindowSeconds` | `300` | 5-minute cooldown prevents flapping during token-generation bursts (queue briefly empties mid-request) |
| `scaleDown.policies[].periodSeconds` | `120` | Remove at most 1 pod per 2 minutes to allow traffic to drain gracefully |

> See [`deploy/autoscaling/hpa.yaml`](deploy/autoscaling/hpa.yaml) for the full manifest with inline documentation.

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
│   │   ├── 01-install-crds.sh               # Gateway API + Inference Extension CRDs
│   │   ├── 02-install-envoy-ai-gateway.sh   # EAG v0.5.0 + EG v1.5.0 + Redis
│   │   ├── 03-install-kuadrant.sh           # Kuadrant (Authorino + Limitador)
│   │   ├── 04-deploy-llm-d.sh               # llm-d helmfile (5 releases)
│   │   └── 05-deploy-services.sh            # Magpie TTS + Riva STT
│   ├── gateway/              # Gateway + AIGatewayRoute + Envoy Gateway values
│   │   ├── namespace.yaml
│   │   ├── gateway.yaml                     # Gateway (gatewayClassName: eg)
│   │   ├── aigwroute.yaml                   # AIGatewayRoute: model → InferencePool
│   │   └── envoy-gateway-values.yaml        # EG helm values: EAG extension manager
│   ├── llm-d/                # Helmfile + values for llm-d 5-release deploy
│   │   ├── helmfile.yaml.gotmpl
│   │   └── values/
│   ├── magpie-tts/           # Magpie TTS deployment + HTTPRoute
│   ├── riva-stt/             # Riva STT → NVIDIA NIM proxy
│   │   ├── backend.yaml      # EG Backend + BackendTLSPolicy + BackendSecurityPolicy
│   │   ├── httproute.yaml    # HTTPRoute: /v1/audio/transcriptions → NVIDIA NIM
│   │   └── secret-template.yaml  # NVIDIA API key Secret template
│   ├── policies/             # Kuadrant AuthPolicy, RateLimitPolicy, TokenRateLimitPolicy
│   ├── tenants/              # Tenant API key Secrets (template + demos)
│   ├── monitoring/           # Prometheus ServiceMonitors (including vLLM GPU metrics)
│   └── autoscaling/          # GPU-aware HPA manifests + Prometheus Adapter values
│       ├── prometheus-adapter-values.yaml  # Custom metrics API bridge (vllm_ → HPA)
│       └── hpa.yaml                        # HPA: scale on queue depth + VRAM pressure
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

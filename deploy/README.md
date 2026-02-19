# TokenLabs Deployment Guide

## Stack

| Layer | Component | Version | Role |
|---|---|---|---|
| **Gateway** | [Envoy AI Gateway](https://aigateway.envoyproxy.io/) | v0.5.0 | AI-aware proxy: body parsing, model routing, FQDN backends |
| **Gateway engine** | [Envoy Gateway](https://gateway.envoyproxy.io/) | v1.5.0 | Kubernetes Gateway API implementation (EAG builds on top of EG) |
| **Tenant controls** | [Kuadrant](https://docs.kuadrant.io/) | latest | API-key auth, per-tenant request + token-based rate limiting |
| **Inference routing** | [llm-d](https://llm-d.ai/) | v0.5.0 | KV-cache & queue-depth aware pod selection via ext_proc (EPP) |

## How Multi-Model Routing Works (no BBR needed)

```
Client  POST /v1/chat/completions  {"model": "nvidia/...", "messages": [...]}
  │
  ▼ Kuadrant AuthPolicy (Authorino validates Bearer token → extracts tier/userid)
  │
  ▼ Kuadrant RateLimitPolicy (Limitador: req count per tier per window)
  │
  ▼ EAG AI filter (reads "model" from JSON body → sets x-ai-eg-model header)
  │     No ConfigMaps, no separate ext_proc sidecar — EAG does this natively.
  │
  ▼ AIGatewayRoute (matches x-ai-eg-model header → selects InferencePool)
  │     nvidia/Llama-3.1-Nemotron-Nano-8B-v1   → token-labs-pool (spark-01)
  │     nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8 → nemotron-vl-pool (spark-02)
  │
  ▼ llm-d EPP (KV-cache + queue aware pod picker within the InferencePool)
  │
  ▼ vLLM worker (generates completion, returns usage.total_tokens)
  │
  ▼ Kuadrant TokenRateLimitPolicy (extracts total_tokens → per-tenant quota)
```

## Prerequisites

- MicroK8s cluster: `controller` (AMD64), `spark-01` (ARM64, GB10 GPU), `spark-02` (ARM64, GB10 GPU)
- `kubectl` configured
- `helm` v3.x + `helmfile`
- NVIDIA GPU Operator running (already installed in `gpu-operator` namespace)


## Deployment Order

```bash
# 1. Install Gateway API CRDs + Inference Extension CRDs
./deploy/scripts/01-install-crds.sh

# 2. Install Envoy AI Gateway (EAG v0.5.0 + EG v1.5.0 + Redis)
./deploy/scripts/02-install-envoy-ai-gateway.sh

# 3. Install Kuadrant (Authorino + Limitador)
./deploy/scripts/03-install-kuadrant.sh

# 4. Deploy llm-d (infra + 2x InferencePools + 2x ModelServices)
./deploy/scripts/04-deploy-llm-d.sh

# 5. Deploy AI Gateway Route (model-based routing to InferencePool)
./deploy/scripts/05-deploy-ai-gateway-route.sh

# 6. On controller: enable Buildx plugin + multiarch builder
sudo apt-get update && sudo apt-get install -y docker-buildx
docker buildx create --use --name multiarch || docker buildx use multiarch
docker buildx inspect --bootstrap

# 7. Build and push Magpie TTS image (spark-01 is ARM64)
docker buildx build \
  --platform linux/arm64 \
  -t ghcr.io/elizabetht/token-labs/magpie-tts:latest \
  --push \
  services/magpie-tts

# 8. Deploy Magpie TTS (text-to-speech service)
kubectl apply -f deploy/magpie-tts/

# 9. Apply Gateway and Kuadrant policies
kubectl apply -f deploy/gateway/gateway.yaml
kubectl apply -f deploy/gateway/namespace.yaml
kubectl apply -f deploy/policies/

# 10. Deploy Magpie TTS + Riva STT proxy
#    (first create NVIDIA API key Secret for Riva STT)
kubectl create secret generic nvidia-nim-api-key \
  --from-literal=apiKey=nvapi-CHANGEME \
  -n token-labs
./deploy/scripts/05-deploy-services.sh

# 11. Create tenant API keys
kubectl apply -f deploy/tenants/
```

## Directory Structure

```
deploy/
├── scripts/             # Installation scripts
│   ├── 01-install-crds.sh
│   ├── 02-install-envoy-gateway.sh
│   ├── 03-install-kuadrant.sh
│   ├── 04-deploy-llm-d.sh
│   └── 05-deploy-ai-gateway-route.sh
├── gateway/             # Gateway + AIGatewayRoute resources
│   ├── namespace.yaml
│   ├── gateway.yaml
│   └── aigatewayroute.yaml
├── llm-d/               # llm-d helmfile + values (5 releases)
│   ├── helmfile.yaml.gotmpl
│   └── values/
│       ├── infra.yaml
│       ├── inferencepool.yaml
│       ├── inferencepool-nemotron-vl.yaml
│       ├── modelservice.yaml
│       └── modelservice-nemotron-vl.yaml
├── magpie-tts/          # Magpie TTS deployment
│   ├── deployment.yaml
│   └── httproute.yaml
├── policies/            # Kuadrant policies
│   ├── kuadrant.yaml
│   ├── auth-policy.yaml
│   ├── rate-limit-policy.yaml
│   └── token-rate-limit-policy.yaml
├── tenants/             # Tenant API key secrets
│   ├── tenant-free-demo.yaml
│   └── tenant-pro-demo.yaml
└── monitoring/          # Observability (optional)
    └── service-monitors.yaml
```

## Verification

```bash
# Check EAG + EG
kubectl get pods -n envoy-ai-gateway-system
kubectl get pods -n envoy-gateway-system
kubectl get gatewayclass

# Check Kuadrant
kubectl get pods -n kuadrant-system

# Check llm-d
kubectl get inferencepool -n token-labs
kubectl get pods -n token-labs

# Check inference pools and AI Gateway route
kubectl get inferencepool -n token-labs
kubectl get aigatewayroute -n token-labs

# Get Gateway IP
kubectl get gateway token-labs-gateway -n token-labs \
  -o jsonpath='{.status.addresses[0].value}'
```

## Testing

```bash
GATEWAY_IP=$(kubectl get gateway token-labs-gateway -n token-labs \
  -o jsonpath='{.status.addresses[0].value}')

# Nemotron-Llama 8B (spark-01)
curl -H "Host: inference.token-labs.local" \
     -H "Authorization: Bearer tlabs_free_demo_key_change_me" \
     -H "Content-Type: application/json" \
     -X POST http://${GATEWAY_IP}/v1/chat/completions \
     -d '{"model": "nvidia/Llama-3.1-Nemotron-Nano-8B-v1",
          "messages": [{"role": "user", "content": "Hello!"}],
          "max_tokens": 100}'

# Nemotron VL 12B FP8 (spark-02)
curl -H "Host: inference.token-labs.local" \
     -H "Authorization: Bearer tlabs_free_demo_key_change_me" \
     -H "Content-Type: application/json" \
     -X POST http://${GATEWAY_IP}/v1/chat/completions \
     -d '{"model": "nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8",
          "messages": [{"role": "user", "content": "Describe this image."}],
          "max_tokens": 100}'

# Magpie TTS
curl -H "Host: inference.token-labs.local" \
     -H "Authorization: Bearer tlabs_free_demo_key_change_me" \
     -H "Content-Type: application/json" \
     -X POST http://${GATEWAY_IP}/v1/audio/speech \
     -d '{"input": "Welcome to Token Labs.", "voice": "aria"}' \
     --output speech.wav

# Riva STT (requires NVIDIA NIM API key)
curl -H "Host: inference.token-labs.local" \
     -H "Authorization: Bearer tlabs_free_demo_key_change_me" \
     -X POST http://${GATEWAY_IP}/v1/audio/transcriptions \
     -F "file=@audio.wav" \
     -F "model=nvidia/parakeet-ctc-1.1b"
```

## GPU Layout

| Node | GPU | vLLM Model | Other |
|---|---|---|---|
| spark-01 | GB10 (72GB) | Nemotron-Llama 8B (80% util) | Magpie TTS (CPU) |
| spark-02 | GB10 (72GB) | Nemotron VL 12B FP8 (90% util) | — |

**GPU sharing**: `nvidia.com/gpu.sharing-strategy=none` on both nodes.
Each node can only run one GPU-requesting pod. Magpie TTS runs CPU-only.
To enable GPU sharing for TTS: configure MPS time-slicing via GPU Operator.

# TokenLabs — Quick Reference

> For full architecture details and step-by-step explanations see the [main README](../README.md).

## Prerequisites

- MicroK8s cluster: `controller` (AMD64), `spark-01` (ARM64, GB10 GPU), `spark-02` (ARM64, GB10 GPU)
- `kubectl` + `helm` v3.x + `helmfile` configured
- HuggingFace token with access to the model weights

## Core Deployment

```bash
# 1. Gateway API CRDs + Inference Extension CRDs
./deploy/scripts/01-install-crds.sh

# 2. Envoy AI Gateway (EAG v0.5.0) + Envoy Gateway (EG v1.5.0) + Redis
./deploy/scripts/02-install-envoy-ai-gateway.sh

# 3. Kuadrant (Authorino + Limitador)
./deploy/scripts/03-install-kuadrant.sh

# 4. llm-d — infra + 2x InferencePools + 2x ModelServices (vLLM workers)
kubectl create namespace token-labs
kubectl create secret generic hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  -n token-labs
./deploy/scripts/04-deploy-llm-d.sh

# 5. Gateway + AIGatewayRoute + Kuadrant policies
kubectl apply -f deploy/gateway/namespace.yaml
kubectl apply -f deploy/gateway/gatewayclass.yaml
kubectl apply -f deploy/gateway/gateway.yaml
kubectl apply -f deploy/gateway/aigwroute.yaml
kubectl apply -f deploy/policies/

# 6. Tenant API keys — generate a key for each tenant before applying
#    Copy deploy/tenants/tenant-template.yaml, replace COMPANY-NAME and api_key, then:
COMPANY="acme"
TIER="pro"   # free | pro | enterprise
API_KEY="tlabs_sk_$(openssl rand -hex 24)"
sed \
  -e "s/COMPANY-NAME/${COMPANY}/g" \
  -e "s/\"pro\"/\"${TIER}\"/g" \
  -e "s/tlabs_CHANGEME/${API_KEY}/g" \
  deploy/tenants/tenant-template.yaml \
  | kubectl apply -f -
echo "Key for ${COMPANY}: ${API_KEY}"
#
# Or edit the demo files and apply the whole directory:
#   kubectl apply -f deploy/tenants/
```

## Optional: Magpie TTS

> Must be built natively on spark-01 (ARM64) — do NOT cross-compile via QEMU on controller.
> Model downloads from NGC on first pod startup (~1–2 min); it is not baked into the image.

```bash
# Build on spark-01 (ssh in first)
docker build -t ghcr.io/elizabetht/token-labs/magpie-tts:latest services/magpie-tts
docker push ghcr.io/elizabetht/token-labs/magpie-tts:latest

# If GHCR package is private, create a pull secret first
kubectl create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username=elizabetht \
  --docker-password=<GITHUB_PAT_read:packages> \
  -n token-labs

# Deploy
kubectl apply -f deploy/magpie-tts/
```

## Optional: Riva STT

```bash
kubectl create secret generic nvidia-nim-api-key \
  --from-literal=apiKey=nvapi-CHANGEME \
  -n token-labs
./deploy/scripts/05-deploy-services.sh
```

## Verification

```bash
kubectl get pods -n envoy-ai-gateway-system   # EAG controller
kubectl get pods -n envoy-gateway-system       # EG controller
kubectl get pods -n kuadrant-system            # Authorino + Limitador
kubectl get pods -n token-labs                 # vLLM workers + EPPs (+ magpie-tts if deployed)
kubectl get inferencepool -n token-labs        # both pools Ready
kubectl get aigatewayroute -n token-labs
kubectl get authpolicy -n token-labs           # Accepted: True
kubectl get ratelimitpolicy -n token-labs      # Accepted: True
kubectl get tokenratelimitpolicy -n token-labs # Accepted: True
```

## GPU Layout

| Node | GPU | vLLM Model | Other |
|---|---|---|---|
| spark-01 | GB10 (72GB) | Nemotron-Llama 8B (80% util) | Magpie TTS (CPU, optional) |
| spark-02 | GB10 (72GB) | Nemotron VL 12B FP8 (90% util) | — |

`nvidia.com/gpu.sharing-strategy=none` on both nodes — one GPU-requesting pod per node.
Magpie TTS runs CPU-only. To enable GPU sharing: configure MPS time-slicing via GPU Operator.

## Directory Structure

```
deploy/
├── scripts/             # Installation scripts (run in order)
│   ├── 01-install-crds.sh
│   ├── 02-install-envoy-ai-gateway.sh
│   ├── 03-install-kuadrant.sh
│   ├── 04-deploy-llm-d.sh
│   └── 05-deploy-services.sh
├── gateway/             # Gateway + AIGatewayRoute resources
│   ├── namespace.yaml
│   ├── gatewayclass.yaml
│   ├── gateway.yaml
│   └── aigwroute.yaml
├── llm-d/               # llm-d helmfile + values (5 releases)
│   ├── helmfile.yaml.gotmpl
│   └── values/
│       ├── infra.yaml
│       ├── inferencepool.yaml
│       ├── inferencepool-nemotron-vl.yaml
│       ├── modelservice.yaml
│       └── modelservice-nemotron-vl.yaml
├── magpie-tts/          # Magpie TTS deployment (optional)
│   ├── deployment.yaml
│   └── httproute.yaml
├── policies/            # Kuadrant policies
│   ├── kuadrant.yaml
│   ├── auth-policy.yaml
│   ├── rate-limit-policy.yaml
│   └── token-rate-limit-policy.yaml
├── tenants/             # Tenant API key secrets
│   ├── tenant-template.yaml
│   ├── tenant-free-demo.yaml
│   └── tenant-pro-demo.yaml
└── monitoring/          # Observability (optional)
    └── service-monitors.yaml
```

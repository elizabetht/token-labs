# TokenLabs Deployment Guide

## Prerequisites

- MicroK8s cluster with GPU workers (spark-01, spark-02)
- `kubectl` configured for the cluster
- `helm` v3.x installed
- `helmfile` installed (for llm-d)

## Deployment Order

```bash
# 1. Install Gateway API CRDs + Inference Extension CRDs
./deploy/scripts/01-install-crds.sh

# 2. Install Envoy Gateway
./deploy/scripts/02-install-envoy-gateway.sh

# 3. Install Kuadrant Operator
./deploy/scripts/03-install-kuadrant.sh

# 4. Deploy llm-d (infra + model services + inference pools)
./deploy/scripts/04-deploy-llm-d.sh

# 5. Apply InferenceModel CRDs (model-based routing)
kubectl apply -f deploy/llm-d/inferencemodels.yaml

# 6. Deploy Magpie TTS (text-to-speech service)
kubectl apply -f deploy/magpie-tts/

# 7. Apply Gateway, HTTPRoute, and Kuadrant policies
kubectl apply -f deploy/gateway/
kubectl apply -f deploy/policies/

# 8. Create tenant API keys
kubectl apply -f deploy/tenants/
```

## Directory Structure

```
deploy/
├── scripts/             # Installation scripts
│   ├── 01-install-crds.sh
│   ├── 02-install-envoy-gateway.sh
│   ├── 03-install-kuadrant.sh
│   └── 04-deploy-llm-d.sh
├── gateway/             # Gateway + HTTPRoute resources
│   ├── namespace.yaml
│   ├── gateway.yaml
│   └── httproute.yaml
├── llm-d/               # llm-d helmfile + values (5 releases)
│   ├── helmfile.yaml.gotmpl
│   ├── inferencemodels.yaml
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
# Check all components are running
kubectl get pods -n envoy-gateway-system
kubectl get pods -n kuadrant-system
kubectl get pods -n token-labs

# Check inference models and pools
kubectl get inferencepool -n token-labs
kubectl get inferencemodel -n token-labs

# Check policies are accepted
kubectl get authpolicy -n token-labs
kubectl get ratelimitpolicy -n token-labs
kubectl get tokenratelimitpolicy -n token-labs

# Test Llama 3.1 8B (chat)
curl -H "Host: inference.token-labs.local" \
     -H "Authorization: APIKEY tlabs_free_demo_key" \
     -H "Content-Type: application/json" \
     -X POST http://<GATEWAY_IP>/v1/chat/completions \
     -d '{
       "model": "meta-llama/Llama-3.1-8B-Instruct",
       "messages": [{"role": "user", "content": "Hello"}],
       "max_tokens": 100
     }'

# Test Nemotron VL 12B (vision-language)
curl -H "Host: inference.token-labs.local" \
     -H "Authorization: APIKEY tlabs_free_demo_key" \
     -H "Content-Type: application/json" \
     -X POST http://<GATEWAY_IP>/v1/chat/completions \
     -d '{
       "model": "nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8",
       "messages": [{"role": "user", "content": "Describe this image."}],
       "max_tokens": 100
     }'

# Test Magpie TTS (text-to-speech)
curl -H "Host: inference.token-labs.local" \
     -H "Authorization: APIKEY tlabs_free_demo_key" \
     -H "Content-Type: application/json" \
     -X POST http://<GATEWAY_IP>/v1/audio/speech \
     -d '{
       "input": "Welcome to Token Labs.",
       "voice": "aria",
       "language": "en"
     }' --output speech.wav
```

# TokenLabs Deployment Guide

## Architecture

```
Client
  │  POST /v1/chat/completions  {"model": "nvidia/Qwen3.5-397B-A17B-NVFP4", ...}
  ▼
Envoy AI Gateway  (parses model field → sets x-ai-eg-model header)
  │
AIGatewayRoute   (matches x-ai-eg-model → routes to InferencePool)
  │
InferencePool + EPP  (llm-d KV-cache-aware scheduling)
  │
LeaderWorkerSet  (spark-01 leader + spark-02 worker, PP=2, NCCL over eth0)
  │  spark-01: Ray head + vLLM API server, pipeline layers 0–N/2
  │  spark-02: Ray worker, pipeline layers N/2–N
  ▼
nvidia/Qwen3.5-397B-A17B-NVFP4  (~200 GB NVFP4, ~100 GB per node)
```

## Models

| Model | Nodes | Strategy | Manifests |
|-------|-------|----------|-----------|
| nvidia/Qwen3.5-397B-A17B-NVFP4 | spark-01 + spark-02 | PP=2 (LeaderWorkerSet) | `deploy/qwen35-397b/` |
| org/gpt-oss-120gb | spark-01 + spark-02 | PP=2 (LeaderWorkerSet) | `deploy/gpt-oss/` |

## Prerequisites

- MicroK8s cluster with GPU workers (`spark-01`, `spark-02`)
- `kubectl` configured for the cluster
- `helm` v3.x + `helmfile` installed
- LeaderWorkerSet controller installed:
  ```bash
  kubectl apply --server-side -f https://github.com/kubernetes-sigs/lws/releases/latest/download/manifests.yaml
  ```

## Deployment Order

### 1. Infrastructure (one-time)

```bash
# Gateway API CRDs + Envoy Gateway
kubectl apply -f deploy/infrastructure/

# Gateway + namespace
kubectl apply -f deploy/gateway/namespace.yaml
kubectl apply -f deploy/gateway/gateway.yaml
kubectl apply -f deploy/gateway/gatewayclass.yaml

# Kuadrant policies
kubectl apply -f deploy/policies/

# Tenant API keys
kubectl apply -f deploy/tenants/
```

### 2. Cluster — disable GPU time-slicing on both nodes

```bash
kubectl label node spark-01 nvidia.com/gpu.sharing-strategy=none --overwrite
kubectl label node spark-02 nvidia.com/gpu.sharing-strategy=none --overwrite
```

### 3. Model deployment — Qwen3.5-397B-A17B-NVFP4

```bash
# Create PVCs (280 Gi per node — weights ~200 GB + HF download headroom)
kubectl apply -f deploy/qwen35-397b/model-cache-pvcs.yaml

# Deploy InferencePool + EPP via helmfile
helmfile -f deploy/llm-d/helmfile.yaml.gotmpl sync

# Deploy AIGatewayRoute (model-based routing)
kubectl apply -f deploy/gateway/aigwroute.yaml

# Deploy LeaderWorkerSet (PP=2 across spark-01 + spark-02)
kubectl apply -f deploy/qwen35-397b/leaderworkerset.yaml
```

> **Note:** On first run vLLM downloads ~200 GB from HuggingFace Hub. The startup probe allows up to ~121 minutes (`initialDelaySeconds: 60`, `failureThreshold: 480`, `periodSeconds: 15`).

### 4. Watch startup

```bash
# Follow leader pod logs
kubectl logs -n token-labs -l app=qwen35-397b,role=leader -f

# Watch pods come up
kubectl get pods -n token-labs -w
```

## Verification

```bash
# Check cluster health
kubectl get nodes
kubectl get pods -n token-labs
kubectl get inferencepool -n token-labs
kubectl get aigatewayroute -n token-labs
kubectl get authpolicy,ratelimitpolicy,tokenratelimitpolicy -n token-labs

# Run the full test suite
bash deploy/tests/01-test-cluster-nodes.sh
bash deploy/tests/02-test-gpu-access.sh
bash deploy/tests/03-test-inference.sh
bash deploy/tests/04-test-token-quota.sh

# Quick inference test
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
curl -s \
  -H "Host: inference.tokenlabs.run" \
  -H "Authorization: APIKEY tlabs_free_demo_key" \
  -H "Content-Type: application/json" \
  -X POST http://${GATEWAY_IP}/v1/chat/completions \
  -d '{
    "model": "nvidia/Qwen3.5-397B-A17B-NVFP4",
    "messages": [{"role": "user", "content": "What is pipeline parallelism?"}],
    "max_tokens": 200
  }' | jq .
```

## Directory Structure

```
deploy/
├── README.md                    # This file
├── kustomization.yaml           # Kustomize root (infrastructure layer)
├── parallelism-strategies.md    # DP / TP / PP / EP reference doc
│
├── cluster/                     # Cluster-level config
│   └── nvidia/
│       └── gpu-sharing/         # Enable / disable GPU time-slicing
│
├── gateway/                     # Envoy Gateway resources
│   ├── namespace.yaml
│   ├── gatewayclass.yaml
│   ├── gateway.yaml
│   └── aigwroute.yaml           # AIGatewayRoute — model → InferencePool routing
│
├── infrastructure/              # Flux-managed operators (CRDs, controllers)
│   ├── kustomization.yaml
│   ├── controllers/
│   └── sources/
│
├── llm-d/                       # llm-d helmfile
│   ├── helmfile.yaml.gotmpl     # InferencePool releases per model
│   └── values/
│       └── infra.yaml           # Shared infra values
│
├── policies/                    # Kuadrant auth + rate-limit policies
│   ├── kuadrant.yaml
│   ├── auth-policy.yaml
│   ├── rate-limit-policy.yaml
│   └── token-rate-limit-policy.yaml
│
├── qwen35-397b/                 # nvidia/Qwen3.5-397B-A17B-NVFP4 (PP=2)
│   ├── model-cache-pvcs.yaml    # 280 Gi PVCs for spark-01 and spark-02
│   ├── leaderworkerset.yaml     # LWS: leader (spark-01) + worker (spark-02)
│   └── inferencepool-values.yaml
│
├── gpt-oss/                     # org/gpt-oss-120gb (PP=2, placeholder)
│   ├── model-cache-pvcs.yaml
│   ├── leaderworkerset.yaml
│   └── inferencepool-values.yaml
│
├── tenants/                     # Tenant API key secrets
│   ├── tenant-template.yaml
│   ├── tenant-free-demo.yaml
│   └── tenant-pro-demo.yaml
│
└── tests/                       # Smoke tests
    ├── README.md
    ├── 01-test-cluster-nodes.sh
    ├── 02-test-gpu-access.sh
    ├── 03-test-inference.sh
    └── 04-test-token-quota.sh
```

## Troubleshooting

**Leader pod stuck in Init / not ready:**
```bash
kubectl describe pod -n token-labs -l app=qwen35-397b,role=leader
kubectl logs -n token-labs -l app=qwen35-397b,role=leader --previous
```

**Worker not joining Ray cluster:**
```bash
kubectl logs -n token-labs -l app=qwen35-397b,role=worker -f
# Should see: "Connecting to Ray head at <leader-ip>:6379"
```

**NCCL errors (GPU P2P):**
NCCL_P2P_DISABLE=1 and NCCL_IB_DISABLE=1 are set — both Sparks communicate over eth0 (ethernet). This is expected; RDMA is not available between nodes in this setup.

**PVC not binding:**
```bash
kubectl get pvc -n token-labs
# Ensure longhorn-model-cache StorageClass exists and Longhorn is running
kubectl get sc longhorn-model-cache
```

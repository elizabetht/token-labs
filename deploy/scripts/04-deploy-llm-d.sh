#!/bin/bash
# 04-deploy-llm-d.sh — Deploy llm-d (infra + model service + inference pool)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LLM_D_DIR="${SCRIPT_DIR}/../llm-d"

echo "==> Creating token-labs namespace..."
kubectl create namespace token-labs --dry-run=client -o yaml | kubectl apply -f -

echo "==> Deploying llm-d via helmfile..."
cd "${LLM_D_DIR}"
helmfile apply

echo "==> Waiting for vLLM workers to be ready..."
kubectl wait --timeout=10m -n token-labs statefulset/llm-d-modelservice --for=jsonpath='{.status.readyReplicas}'=2 2>/dev/null || \
  echo "    (Workers may take several minutes to download model weights)"

echo "==> Waiting for EPP to be ready..."
kubectl wait --timeout=5m -n token-labs deployment/llm-d-epp --for=condition=Available 2>/dev/null || true

echo "==> llm-d deployment status:"
kubectl get pods -n token-labs
kubectl get inferencepool -n token-labs
kubectl get inferencemodel -n token-labs

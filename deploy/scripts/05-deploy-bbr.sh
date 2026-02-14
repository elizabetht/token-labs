#!/bin/bash
# 05-deploy-bbr.sh — Deploy the Body Based Router (BBR) for multi-model routing
#
# In v1.3.0 of the Gateway API Inference Extension, the InferenceModel CRD
# has been removed. Multi-model routing is now handled by the BBR extension,
# which extracts the "model" field from the request body, maps it to a base
# model via ConfigMaps, and sets the X-Gateway-Base-Model-Name header.
# The HTTPRoute then matches on this header to route to the correct InferencePool.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BBR_DIR="${SCRIPT_DIR}/../bbr"

BBR_VERSION="v1.3.0"
BBR_CHART="oci://registry.k8s.io/gateway-api-inference-extension/charts/body-based-routing"

echo "==> Installing Body Based Router (BBR) v${BBR_VERSION}..."
helm upgrade --install body-based-router "${BBR_CHART}" \
  --version "${BBR_VERSION}" \
  --namespace token-labs \
  --create-namespace \
  --set provider.name=none \
  --set inferenceGateway.name=token-labs-gateway

echo "==> Applying BBR model mapping ConfigMaps..."
kubectl apply -f "${BBR_DIR}/configmaps.yaml"

echo "==> Applying EnvoyExtensionPolicy for BBR ext_proc..."
kubectl apply -f "${BBR_DIR}/envoy-extension-policy.yaml"

echo "==> Waiting for BBR to be ready..."
kubectl wait --timeout=2m -n token-labs deployment/body-based-router \
  --for=condition=Available 2>/dev/null || true

echo "==> BBR deployment status:"
kubectl get pods -n token-labs -l app=body-based-router
kubectl get envoyextensionpolicy -n token-labs

echo "==> BBR deployed successfully"

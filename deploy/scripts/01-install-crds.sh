#!/bin/bash
# 01-install-crds.sh — Install Gateway API and Inference Extension CRDs
#
# Versions:
#   Gateway API:             v1.4.1 (standard channel)
#   Inference Extension:     v1.3.0 (includes InferencePool, InferenceObjective)
#
# The Inference Extension CRDs are required by:
#   - llm-d (InferencePool, EPP routing)
#   - Envoy AI Gateway (InferencePool as backendRef in AIGatewayRoute)
set -euo pipefail

echo "==> Installing Gateway API CRDs (v1.4.1)..."
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.1/standard-install.yaml

echo "==> Installing Gateway API Inference Extension CRDs (v1.3.0)..."
# Installs: InferencePool, InferenceObjective (replaces InferenceModel from v0.x)
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.3.0/manifests.yaml

echo "==> Verifying CRDs..."
kubectl get crd gateways.gateway.networking.k8s.io
kubectl get crd httproutes.gateway.networking.k8s.io
kubectl get crd inferencepools.inference.networking.k8s.io
kubectl get crd inferenceobjectives.inference.networking.k8s.io 2>/dev/null || \
  echo "    (InferenceObjective CRD not yet in this release — OK if using v0.x inference extension)"

echo "==> CRDs installed successfully"

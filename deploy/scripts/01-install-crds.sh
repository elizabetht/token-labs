#!/bin/bash
# 01-install-crds.sh — Install Gateway API, Inference Extension, and AI Gateway CRDs
set -euo pipefail

AIGW_VERSION="v0.5.0"

echo "==> Installing Gateway API CRDs..."
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.1/standard-install.yaml

echo "==> Installing Gateway API Inference Extension CRDs..."
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.3.0/manifests.yaml

echo "==> Installing Envoy AI Gateway CRDs..."
helm upgrade -i aieg-crd oci://docker.io/envoyproxy/ai-gateway-crds-helm \
  --version "${AIGW_VERSION}" \
  --namespace envoy-ai-gateway-system \
  --create-namespace

echo "==> Verifying CRDs..."
kubectl get crd gateways.gateway.networking.k8s.io
kubectl get crd httproutes.gateway.networking.k8s.io
kubectl get crd inferencepools.inference.networking.k8s.io
kubectl get crd aigatewayroutes.aigateway.envoyproxy.io

echo "==> CRDs installed successfully"

#!/bin/bash
# 01-install-crds.sh — Install Gateway API and Inference Extension CRDs
set -euo pipefail

echo "==> Installing Gateway API CRDs..."
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml

echo "==> Installing Gateway API Inference Extension CRDs..."
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v0.4.0/manifests.yaml

echo "==> Verifying CRDs..."
kubectl get crd gateways.gateway.networking.k8s.io
kubectl get crd httproutes.gateway.networking.k8s.io
kubectl get crd inferencepools.inference.networking.x-k8s.io
kubectl get crd inferencemodels.inference.networking.x-k8s.io

echo "==> CRDs installed successfully"

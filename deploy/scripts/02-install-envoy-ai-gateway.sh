#!/bin/bash
# 02-install-envoy-ai-gateway.sh
#
# Installs Envoy AI Gateway (EAG) v0.5.0 on top of Envoy Gateway v1.5.0.
#
# Architecture:
#   EAG controller  (envoy-ai-gateway-system)
#     ↑ extends via xDS hooks
#   Envoy Gateway   (envoy-gateway-system, gatewayClassName: eg)
#     ↑ implements
#   Gateway API CRDs
#
# What EAG adds over plain EG:
#   - AIGatewayRoute CRD: routes based on x-ai-eg-model header (extracted from JSON body)
#   - AIServiceBackend CRD: external AI provider backends (NVIDIA NIM, OpenAI, etc.)
#   - BackendSecurityPolicy CRD: injects provider credentials (API key) toward backends
#   - InferencePool as valid backendRef (llm-d EPP routing)
#   - Native body parsing — replaces the BBR (Body Based Router) ext_proc sidecar
#
# Install order matters:
#   1. EAG CRDs  — registers AIGatewayRoute, AIServiceBackend, BackendSecurityPolicy
#   2. EAG controller — creates the envoy-ai-gateway-tls cert Secret, starts AI controller
#   3. EG — configured to connect to EAG controller via extensionManager (reads that cert)
#   4. Redis — for EG/Kuadrant rate-limit counters
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATEWAY_DIR="${SCRIPT_DIR}/../gateway"

EAG_VERSION="v0.5.0"
EG_VERSION="v1.5.0"

echo "==> [1/4] Installing Envoy AI Gateway CRDs (${EAG_VERSION})..."
helm upgrade -i aieg-crd oci://docker.io/envoyproxy/ai-gateway-crds-helm \
  --version "${EAG_VERSION}" \
  --namespace envoy-ai-gateway-system \
  --create-namespace

echo "==> [2/4] Installing Envoy AI Gateway controller (${EAG_VERSION})..."
helm upgrade -i aieg oci://docker.io/envoyproxy/ai-gateway-helm \
  --version "${EAG_VERSION}" \
  --namespace envoy-ai-gateway-system \
  --create-namespace

echo "==> Waiting for EAG controller to be ready (creates TLS cert Secret)..."
kubectl wait --timeout=3m \
  -n envoy-ai-gateway-system \
  deployment/ai-gateway-controller \
  --for=condition=Available

echo "==> [3/4] Installing Envoy Gateway (${EG_VERSION}) with EAG extension manager values..."
# The values file enables:
#   - extensionManager → EAG controller (xDS hooks for AI routing)
#   - backendResources: InferencePool (llm-d EPP as backendRef)
#   - enableBackend: true (FQDN backends for Riva STT → NVIDIA NIM)
helm upgrade -i eg oci://docker.io/envoyproxy/gateway-helm \
  --version "${EG_VERSION}" \
  --namespace envoy-gateway-system \
  --create-namespace \
  -f "${GATEWAY_DIR}/envoy-gateway-values.yaml"

echo "==> Waiting for Envoy Gateway to be ready..."
kubectl wait --timeout=5m \
  -n envoy-gateway-system \
  deployment/envoy-gateway \
  --for=condition=Available

echo "==> [4/4] Installing Redis for rate-limit counters..."
kubectl create namespace redis-system --dry-run=client -o yaml | kubectl apply -f -

cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: redis-system
  labels:
    app: redis
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 500m
            memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: redis-system
spec:
  selector:
    app: redis
  ports:
  - port: 6379
    targetPort: 6379
EOF

echo "==> Verifying installation..."
echo "    EAG pods:"
kubectl get pods -n envoy-ai-gateway-system
echo "    EG pods:"
kubectl get pods -n envoy-gateway-system
echo "    GatewayClasses:"
kubectl get gatewayclass

echo "==> Envoy AI Gateway installed successfully"
echo "    EAG version: ${EAG_VERSION}"
echo "    EG version:  ${EG_VERSION}"
echo ""
echo "    New CRDs available:"
echo "      kubectl get aigatewayroutes    -A"
echo "      kubectl get aiservicebackends  -A"
echo "      kubectl get backendsecuritypolicies -A"

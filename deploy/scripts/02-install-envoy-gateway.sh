#!/bin/bash
# 02-install-envoy-gateway.sh — Install Envoy Gateway + AI Gateway + Redis for rate limiting
#
# Envoy AI Gateway (v0.5.0) is built on top of Envoy Gateway. It adds AI-specific
# capabilities: AIGatewayRoute for model-based routing, token cost tracking,
# and provider integration. This replaces the need for the BBR ext_proc.
set -euo pipefail

EG_VERSION="v1.6.4"
AIGW_VERSION="v0.5.0"
AIGW_REF="v0.5.0"  # git ref for raw.githubusercontent.com URLs

echo "==> Installing Redis for global rate limiting..."
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

echo "==> Installing Envoy Gateway ${EG_VERSION} with AI Gateway configuration..."
# Install EG with:
#   1. AI Gateway base values (extension manager for AIGatewayRoute support)
#   2. Rate limiting addon (Redis backend for token-based rate limiting)
#   3. InferencePool addon (backend resource support for inference.networking.k8s.io/v1)
helm upgrade -i eg oci://docker.io/envoyproxy/gateway-helm \
  --version "${EG_VERSION}" \
  --namespace envoy-gateway-system \
  --create-namespace \
  -f "https://raw.githubusercontent.com/envoyproxy/ai-gateway/${AIGW_REF}/manifests/envoy-gateway-values.yaml" \
  -f "https://raw.githubusercontent.com/envoyproxy/ai-gateway/${AIGW_REF}/examples/token_ratelimit/envoy-gateway-values-addon.yaml" \
  -f "https://raw.githubusercontent.com/envoyproxy/ai-gateway/${AIGW_REF}/examples/inference-pool/envoy-gateway-values-addon.yaml"

echo "==> Waiting for Envoy Gateway to be ready..."
kubectl wait --timeout=5m -n envoy-gateway-system deployment/envoy-gateway --for=condition=Available

echo "==> Installing Envoy AI Gateway controller ${AIGW_VERSION}..."
helm upgrade -i aieg oci://docker.io/envoyproxy/ai-gateway-helm \
  --version "${AIGW_VERSION}" \
  --namespace envoy-ai-gateway-system \
  --create-namespace

echo "==> Waiting for AI Gateway controller to be ready..."
kubectl wait --timeout=2m -n envoy-ai-gateway-system deployment/ai-gateway-controller --for=condition=Available

echo "==> Verifying installation..."
kubectl get pods -n envoy-gateway-system
kubectl get pods -n envoy-ai-gateway-system

echo "==> Envoy Gateway + AI Gateway installed successfully"

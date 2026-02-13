#!/bin/bash
# 02-install-envoy-gateway.sh — Install Envoy Gateway + Redis for rate limiting
set -euo pipefail

echo "==> Installing Envoy Gateway..."
helm install eg oci://docker.io/envoyproxy/gateway-helm \
  --version v1.3.0 \
  --create-namespace \
  -n envoy-gateway-system \
  --set config.envoyGateway.gateway.controllerName=gateway.envoyproxy.io/gatewayclass-controller

echo "==> Waiting for Envoy Gateway to be ready..."
kubectl wait --timeout=5m -n envoy-gateway-system deployment/envoy-gateway --for=condition=Available

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

echo "==> Enabling global rate limiting in Envoy Gateway..."
helm upgrade eg oci://docker.io/envoyproxy/gateway-helm \
  --version v1.3.0 \
  --set config.envoyGateway.rateLimit.backend.type=Redis \
  --set config.envoyGateway.rateLimit.backend.redis.url="redis.redis-system.svc.cluster.local:6379" \
  --reuse-values \
  -n envoy-gateway-system

kubectl rollout restart deployment envoy-gateway -n envoy-gateway-system
kubectl wait --timeout=5m -n envoy-gateway-system deployment/envoy-gateway --for=condition=Available

echo "==> Envoy Gateway installed successfully"

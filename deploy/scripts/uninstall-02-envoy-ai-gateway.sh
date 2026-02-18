#!/bin/bash
# uninstall-02-envoy-ai-gateway.sh
set -euo pipefail
echo "==> Uninstalling Envoy Gateway..."
helm uninstall eg -n envoy-gateway-system 2>/dev/null || true
echo "==> Uninstalling Envoy AI Gateway controller..."
helm uninstall aieg -n envoy-ai-gateway-system 2>/dev/null || true
echo "==> Uninstalling Envoy AI Gateway CRDs..."
helm uninstall aieg-crd -n envoy-ai-gateway-system 2>/dev/null || true
echo "==> Deleting namespaces..."
kubectl delete namespace envoy-gateway-system --ignore-not-found
kubectl delete namespace envoy-ai-gateway-system --ignore-not-found
kubectl delete namespace redis-system --ignore-not-found
echo "==> Done"

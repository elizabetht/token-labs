#!/bin/bash
# uninstall-02-envoy-gateway.sh — Remove Envoy Gateway + Redis
set -euo pipefail

echo "==> Removing Gateway and HTTPRoute resources..."
kubectl delete -f "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../gateway/" --ignore-not-found 2>/dev/null || true

echo "==> Uninstalling Envoy Gateway Helm release..."
helm uninstall eg -n envoy-gateway-system --ignore-not-found || true

echo "==> Deleting envoy-gateway-system namespace..."
kubectl delete namespace envoy-gateway-system --ignore-not-found

echo "==> Removing Redis..."
kubectl delete namespace redis-system --ignore-not-found

echo "==> Envoy Gateway and Redis removed successfully"

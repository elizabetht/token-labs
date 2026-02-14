#!/bin/bash
# uninstall-05-bbr.sh — Remove the Body Based Router (BBR)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BBR_DIR="${SCRIPT_DIR}/../bbr"

echo "==> Removing BBR EnvoyExtensionPolicy..."
kubectl delete -f "${BBR_DIR}/envoy-extension-policy.yaml" --ignore-not-found

echo "==> Removing BBR model mapping ConfigMaps..."
kubectl delete -f "${BBR_DIR}/configmaps.yaml" --ignore-not-found

echo "==> Uninstalling BBR Helm release..."
helm uninstall body-based-router -n token-labs 2>/dev/null || true

echo "==> BBR removed successfully"

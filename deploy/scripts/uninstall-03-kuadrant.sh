#!/bin/bash
# uninstall-03-kuadrant.sh — Remove Kuadrant Operator (Authorino + Limitador)
set -euo pipefail

echo "==> Removing Kuadrant policies..."
kubectl delete -f "${SCRIPT_DIR:=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}/../policies/" --ignore-not-found 2>/dev/null || true

echo "==> Removing tenant secrets..."
kubectl delete -f "${SCRIPT_DIR}/../tenants/" --ignore-not-found 2>/dev/null || true

echo "==> Removing Kuadrant CR..."
kubectl delete kuadrant kuadrant -n kuadrant-system --ignore-not-found

echo "==> Uninstalling Kuadrant Operator Helm release..."
helm uninstall kuadrant-operator -n kuadrant-system --ignore-not-found || true

echo "==> Deleting kuadrant-system namespace..."
kubectl delete namespace kuadrant-system --ignore-not-found

echo "==> Kuadrant removed successfully"

#!/bin/bash
# uninstall-05-ai-gateway-route.sh — Remove the AIGatewayRoute
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATEWAY_DIR="${SCRIPT_DIR}/../gateway"

echo "==> Removing AIGatewayRoute..."
kubectl delete -f "${GATEWAY_DIR}/aigatewayroute.yaml" --ignore-not-found

echo "==> AIGatewayRoute removed successfully"

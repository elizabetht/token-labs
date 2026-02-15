#!/bin/bash
# 05-deploy-ai-gateway-route.sh — Deploy the AIGatewayRoute for multi-model routing
#
# The Envoy AI Gateway controller handles model-based routing natively via the
# AIGatewayRoute CRD. It extracts the "model" field from the request body, sets
# the x-ai-eg-model header, and routes to the matching InferencePool backend.
#
# This replaces the previous BBR (Body Based Router) + HTTPRoute + ConfigMaps approach.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATEWAY_DIR="${SCRIPT_DIR}/../gateway"

echo "==> Applying AIGatewayRoute..."
kubectl apply -f "${GATEWAY_DIR}/aigatewayroute.yaml"

echo "==> Verifying AIGatewayRoute..."
kubectl get aigatewayroute -n token-labs

echo "==> AIGatewayRoute deployed successfully"

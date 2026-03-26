#!/bin/bash
# 05-deploy-services.sh — Deploy Nemotron VL ClusterIP service
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Deploying Nemotron VL vLLM ClusterIP service..."
kubectl apply -f "${SCRIPT_DIR}/../nemotron-vl/"

echo ""
echo "==> Done."

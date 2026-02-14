#!/bin/bash
# uninstall-all.sh — Remove the entire TokenLabs stack (reverse order)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "  TokenLabs Full Stack Uninstall"
echo "=========================================="
echo ""
echo "This will remove ALL TokenLabs components:"
echo "  • llm-d (vLLM workers, EPPs, Magpie TTS)"
echo "  • BBR (Body Based Router for multi-model routing)"
echo "  • Kuadrant (Authorino, Limitador, policies, tenant secrets)"
echo "  • Envoy Gateway + Redis"
echo "  • Gateway API CRDs"
echo ""
read -r -p "Are you sure? [y/N] " confirm
if [[ "$confirm" != [yY] ]]; then
  echo "Aborted."
  exit 0
fi

echo ""
echo "==> Step 1/6: Removing llm-d..."
"${SCRIPT_DIR}/uninstall-04-llm-d.sh"

echo ""
echo "==> Step 2/6: Removing BBR..."
"${SCRIPT_DIR}/uninstall-05-bbr.sh"

echo ""
echo "==> Step 3/6: Removing Kuadrant..."
"${SCRIPT_DIR}/uninstall-03-kuadrant.sh"

echo ""
echo "==> Step 4/6: Removing Envoy Gateway + Redis..."
"${SCRIPT_DIR}/uninstall-02-envoy-gateway.sh"

echo ""
echo "==> Step 5/6: Removing CRDs..."
"${SCRIPT_DIR}/uninstall-01-crds.sh"

echo ""
echo "==> Step 6/6: Cleaning up token-labs namespace..."
kubectl delete namespace token-labs --ignore-not-found

echo ""
echo "=========================================="
echo "  TokenLabs stack fully removed"
echo "=========================================="

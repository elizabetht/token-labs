#!/bin/bash
# uninstall-01-crds.sh — Remove Gateway API and Inference Extension CRDs
# WARNING: This removes CRDs and ALL instances of these resources cluster-wide.
#          Only run this if no other components depend on these CRDs.
set -euo pipefail

echo "⚠️  WARNING: This will delete all Gateway, HTTPRoute, and InferencePool"
echo "   resources across ALL namespaces. Press Ctrl+C to abort."
read -r -p "   Continue? [y/N] " confirm
if [[ "$confirm" != [yY] ]]; then
  echo "Aborted."
  exit 0
fi

echo "==> Removing Inference Extension CRDs..."
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.3.0/manifests.yaml --ignore-not-found

echo "==> Removing Gateway API CRDs..."
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.1/standard-install.yaml --ignore-not-found

echo "==> CRDs removed successfully"

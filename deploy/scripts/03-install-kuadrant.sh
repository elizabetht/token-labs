#!/bin/bash
# 03-install-kuadrant.sh — Install Kuadrant Operator (Authorino + Limitador)
set -euo pipefail

echo "==> Installing Kuadrant Operator..."
# Install via Helm (see https://docs.kuadrant.io/latest/install-helm/)
helm repo add kuadrant https://kuadrant.io/helm-charts/
helm repo update

helm install kuadrant-operator kuadrant/kuadrant-operator \
  --create-namespace \
  -n kuadrant-system

echo "==> Waiting for Kuadrant Operator to be ready..."
kubectl wait --timeout=5m -n kuadrant-system deployment/kuadrant-operator-controller-manager --for=condition=Available

echo "==> Creating Kuadrant instance..."
cat <<EOF | kubectl apply -f -
apiVersion: kuadrant.io/v1beta1
kind: Kuadrant
metadata:
  name: kuadrant
  namespace: kuadrant-system
EOF

echo "==> Waiting for Kuadrant components (Authorino + Limitador)..."
sleep 10
kubectl wait --timeout=5m -n kuadrant-system deployment/authorino --for=condition=Available 2>/dev/null || true
kubectl wait --timeout=5m -n kuadrant-system deployment/limitador --for=condition=Available 2>/dev/null || true

echo "==> Kuadrant installed successfully"
echo "    Components:"
kubectl get pods -n kuadrant-system

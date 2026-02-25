#!/bin/bash
# 05-deploy-services.sh — Deploy Magpie TTS and Riva STT proxy
#
# Magpie TTS:  local deployment on spark-01 (CPU mode — GPU is taken by vLLM)
# Riva STT:    proxy to NVIDIA NIM at integrate.api.nvidia.com
#              requires NVIDIA API key Secret (see deploy/riva-stt/secret-template.yaml)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Deploying Nemotron VL vLLM ClusterIP service..."
kubectl apply -f "${SCRIPT_DIR}/../nemotron-vl/"

echo "==> Deploying Magpie TTS (CPU mode on spark-01)..."
kubectl apply -f "${SCRIPT_DIR}/../magpie-tts/"

echo "==> Waiting for Magpie TTS to be ready..."
kubectl wait --timeout=3m -n token-labs deployment/magpie-tts --for=condition=Available 2>/dev/null || \
  echo "    (Magpie TTS may need time to pull image)"

echo ""
echo "==> Deploying Riva STT proxy → NVIDIA NIM (integrate.api.nvidia.com)..."

# Check NVIDIA API key Secret exists
if ! kubectl get secret nvidia-nim-api-key -n token-labs &>/dev/null; then
  echo ""
  echo "  ⚠️  NVIDIA API key Secret not found. Create it before applying:"
  echo "     kubectl create secret generic nvidia-nim-api-key \\"
  echo "       --from-literal=apiKey=nvapi-xxxxxxxxxxxxxxxxxxxx \\"
  echo "       -n token-labs"
  echo ""
  echo "  Applying Riva STT backend/route resources (Secret required for BackendSecurityPolicy to work)..."
fi

kubectl apply -f "${SCRIPT_DIR}/../riva-stt/backend.yaml"
kubectl apply -f "${SCRIPT_DIR}/../riva-stt/httproute.yaml"

echo ""
echo "==> Service deployment status:"
kubectl get pods -n token-labs -l app=magpie-tts
kubectl get httproute riva-stt -n token-labs 2>/dev/null || true
kubectl get backendsecuritypolicy nvidia-nim-api-key -n token-labs 2>/dev/null || true

echo ""
echo "==> Done. Test with:"
echo "  # Magpie TTS:"
echo "  curl -H 'Host: inference.token-labs.local' \\"
echo "       -H 'Authorization: Bearer <api_key>' \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -X POST http://<GATEWAY_IP>/v1/audio/speech \\"
echo "       -d '{\"input\": \"Hello world\", \"voice\": \"aria\"}' \\"
echo "       --output speech.wav"
echo ""
echo "  # Riva STT:"
echo "  curl -H 'Host: inference.token-labs.local' \\"
echo "       -H 'Authorization: Bearer <api_key>' \\"
echo "       -X POST http://<GATEWAY_IP>/v1/audio/transcriptions \\"
echo "       -F 'file=@audio.wav' -F 'model=nvidia/parakeet-ctc-1.1b'"

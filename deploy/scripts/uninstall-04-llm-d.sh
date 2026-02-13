#!/bin/bash
# uninstall-04-llm-d.sh — Remove llm-d (inference stack)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LLM_D_DIR="${SCRIPT_DIR}/../llm-d"

echo "==> Removing InferenceModel CRDs..."
kubectl delete -f "${LLM_D_DIR}/inferencemodels.yaml" --ignore-not-found

echo "==> Removing Magpie TTS..."
kubectl delete -f "${SCRIPT_DIR}/../magpie-tts/" --ignore-not-found

echo "==> Destroying llm-d helmfile releases..."
cd "${LLM_D_DIR}"
helmfile destroy || true

echo "==> Removing HuggingFace token secret..."
kubectl delete secret hf-token -n token-labs --ignore-not-found

echo "==> llm-d removed successfully"
echo "    Namespace token-labs still exists (other resources may depend on it)"

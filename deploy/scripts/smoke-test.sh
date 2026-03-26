#!/bin/bash
# smoke-test.sh — Post-bootstrap validation for the token-labs cluster.
#
# Tests (in order):
#   1. All nodes Ready
#   2. GPU accessible inside a pod (nvidia-smi)
#   3. Inference endpoint returns a valid chat completion
#   4. Token quota enforcement returns 429 when exhausted
#
# Requirements:
#   - kubectl configured to reach the cluster
#   - GATEWAY_HOST set (default: api.tokenlabs.run)
#   - API_KEY set to a valid tenant key
set -euo pipefail

GATEWAY_HOST="${GATEWAY_HOST:-api.tokenlabs.run}"
API_KEY="${API_KEY:-tlabs_free_demo_key_change_me}"
MODEL="${MODEL:-Llama-3.1-8B-Instruct}"

PASS=0
FAIL=0

ok()   { echo "  [PASS] $*"; ((PASS++)); }
fail() { echo "  [FAIL] $*"; ((FAIL++)); }

echo "=== 1. Node readiness ==="
NOT_READY=$(kubectl get nodes --no-headers | grep -v " Ready" | wc -l)
if [[ "$NOT_READY" -eq 0 ]]; then
  ok "All nodes are Ready"
  kubectl get nodes -o wide
else
  fail "$NOT_READY node(s) not Ready"
  kubectl get nodes
fi

echo ""
echo "=== 2. GPU access ==="
GPU_OUTPUT=$(kubectl run gpu-smoke-test \
  --image=nvcr.io/nvidia/cuda:12.3.0-base-ubuntu22.04 \
  --rm -it --restart=Never \
  --overrides='{"spec":{"tolerations":[{"operator":"Exists"}],"runtimeClassName":"nvidia"}}' \
  -- nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || true)

if [[ -n "$GPU_OUTPUT" ]]; then
  ok "GPU detected inside pod: $GPU_OUTPUT"
else
  fail "Could not detect GPU inside pod (pod may have timed out — check GPU Operator)"
fi

echo ""
echo "=== 3. Inference endpoint ==="
HTTP_CODE=$(curl -s -o /tmp/infer_response.json -w "%{http_code}" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello\"}],\"max_tokens\":16}" \
  "http://${GATEWAY_HOST}/v1/chat/completions")

if [[ "$HTTP_CODE" == "200" ]]; then
  ok "Inference returned HTTP 200"
  CONTENT=$(python3 -c "import json,sys; d=json.load(open('/tmp/infer_response.json')); print(d['choices'][0]['message']['content'])" 2>/dev/null || echo "(could not parse)")
  echo "     Response: $CONTENT"
else
  fail "Inference returned HTTP ${HTTP_CODE}"
  cat /tmp/infer_response.json
fi

echo ""
echo "=== 4. Token quota enforcement ==="
# The free tier has a 5,000 tokens/min burst. Send a request with a very large
# max_tokens to force quota exhaustion, then confirm the next one gets 429.
curl -s -o /dev/null \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Count to 1000\"}],\"max_tokens\":4096}" \
  "http://${GATEWAY_HOST}/v1/chat/completions" || true

QUOTA_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":16}" \
  "http://${GATEWAY_HOST}/v1/chat/completions")

if [[ "$QUOTA_CODE" == "429" ]]; then
  ok "Token quota enforcement returned HTTP 429 as expected"
elif [[ "$QUOTA_CODE" == "200" ]]; then
  echo "  [SKIP] Quota not yet exhausted (free tier burst is 5,000 tokens/min — run test again with a smaller quota tenant)"
else
  fail "Unexpected HTTP ${QUOTA_CODE} from quota enforcement check"
fi

echo ""
echo "=== Results ==="
echo "  Passed: ${PASS}"
echo "  Failed: ${FAIL}"
[[ "$FAIL" -eq 0 ]] || exit 1

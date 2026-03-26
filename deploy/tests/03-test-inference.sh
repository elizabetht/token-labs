#!/bin/bash
# Inference test: Test model inference through Envoy Gateway
# Usage: ./03-test-inference.sh [API_KEY]

set -e

echo "=== Inference Test ==="
echo

# Get Gateway service endpoint
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system envoy-token-labs-ai-gateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")

if [ -z "$GATEWAY_IP" ]; then
    # Try NodePort if LoadBalancer is not available
    NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
    if [ -z "$NODE_IP" ]; then
        NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
    fi
    NODE_PORT=$(kubectl get svc -n envoy-gateway-system envoy-token-labs-ai-gateway -o jsonpath='{.spec.ports[0].nodePort}')
    GATEWAY_ENDPOINT="${NODE_IP}:${NODE_PORT}"
else
    GATEWAY_ENDPOINT="${GATEWAY_IP}:80"
fi

echo "Gateway endpoint: $GATEWAY_ENDPOINT"

# API Key
API_KEY="${1:-test-api-key}"

# Test request
echo
echo "Sending inference request..."
echo

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "http://${GATEWAY_ENDPOINT}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [
      {"role": "user", "content": "Say hello in one word"}
    ],
    "max_tokens": 10
  }')

# Split response and status code
HTTP_BODY=$(echo "$RESPONSE" | head -n -1)
HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)

echo "HTTP Status: $HTTP_CODE"
echo "Response:"
echo "$HTTP_BODY" | jq . 2>/dev/null || echo "$HTTP_BODY"

if [ "$HTTP_CODE" -eq 200 ]; then
    # Check if response contains expected fields
    if echo "$HTTP_BODY" | jq -e '.choices[0].message.content' > /dev/null 2>&1; then
        CONTENT=$(echo "$HTTP_BODY" | jq -r '.choices[0].message.content')
        echo
        echo "✅ SUCCESS: Received valid chat completion response"
        echo "Model response: $CONTENT"
        exit 0
    else
        echo
        echo "❌ FAIL: Response missing expected fields"
        exit 1
    fi
else
    echo
    echo "❌ FAIL: HTTP request failed with status $HTTP_CODE"
    exit 1
fi

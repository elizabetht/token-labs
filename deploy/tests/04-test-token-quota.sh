#!/bin/bash
# Token quota test: Verify rate limiting by exhausting quota
# Usage: ./04-test-token-quota.sh [API_KEY] [QUOTA_LIMIT]

set -e

echo "=== Token Quota Test ==="
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

# API Key and quota limit
API_KEY="${1:-test-api-key}"
QUOTA_LIMIT="${2:-1000}"

echo "API Key: $API_KEY"
echo "Expected quota limit: $QUOTA_LIMIT tokens"
echo

# Function to make a request and return HTTP status
make_request() {
    curl -s -w "%{http_code}" -o /tmp/quota-test-response.json -X POST "http://${GATEWAY_ENDPOINT}/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${API_KEY}" \
      -d '{
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "messages": [
          {"role": "user", "content": "Count from 1 to 100"}
        ],
        "max_tokens": 200
      }'
}

# Make requests until quota is exhausted
echo "Making requests to exhaust quota..."
request_count=0
total_tokens=0

while [ $total_tokens -lt $((QUOTA_LIMIT + 500)) ]; do
    request_count=$((request_count + 1))

    HTTP_CODE=$(make_request)

    if [ "$HTTP_CODE" -eq 429 ]; then
        echo
        echo "✅ SUCCESS: Received 429 (Too Many Requests) after $request_count requests"
        echo "Token quota enforcement is working!"

        # Show response
        echo
        echo "Rate limit response:"
        cat /tmp/quota-test-response.json | jq . 2>/dev/null || cat /tmp/quota-test-response.json

        rm -f /tmp/quota-test-response.json
        exit 0
    elif [ "$HTTP_CODE" -eq 200 ]; then
        # Extract token usage
        usage=$(jq -r '.usage.total_tokens // 0' /tmp/quota-test-response.json)
        total_tokens=$((total_tokens + usage))
        echo "Request $request_count: HTTP 200, used $usage tokens (total: $total_tokens)"
    else
        echo "Request $request_count: HTTP $HTTP_CODE (unexpected)"
        cat /tmp/quota-test-response.json
        echo
    fi

    # Small delay between requests
    sleep 0.5
done

echo
echo "❌ FAIL: Did not receive 429 status after $request_count requests ($total_tokens tokens)"
echo "Token quota might not be configured correctly"

rm -f /tmp/quota-test-response.json
exit 1

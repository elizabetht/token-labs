#!/usr/bin/env bash
# Functional test to verify LMCache configuration is baked into v0.2.0 Docker image
set -euo pipefail

echo "=========================================="
echo "Testing LMCache Configuration in Docker Image"
echo "=========================================="

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test results
TESTS_PASSED=0
TESTS_FAILED=0

# Function to check test result
check_result() {
    local test_name="$1"
    local result="$2"
    local expected="$3"
    
    if [[ "$result" == "$expected" ]]; then
        echo -e "${GREEN}✓ PASS${NC}: $test_name"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $test_name"
        echo "  Expected: $expected"
        echo "  Got: $result"
        ((TESTS_FAILED++))
        return 1
    fi
}

# Function to check if value contains expected string
check_contains() {
    local test_name="$1"
    local result="$2"
    local expected="$3"
    
    if echo "$result" | grep -q "$expected"; then
        echo -e "${GREEN}✓ PASS${NC}: $test_name"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $test_name"
        echo "  Expected to contain: $expected"
        echo "  Got: $result"
        ((TESTS_FAILED++))
        return 1
    fi
}

# Check if Docker image name is provided
if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Usage: $0 <docker-image-tag>${NC}"
    echo "Example: $0 ghcr.io/elizabetht/token-labs/vllm-serve:v0.2.0"
    exit 1
fi

IMAGE_TAG="$1"

echo ""
echo "Testing Docker image: $IMAGE_TAG"
echo ""

# Test 1: Verify LMCache config file exists in the image
echo "Test 1: Checking if LMCache config file exists..."
CONFIG_CHECK=$(docker run --rm "$IMAGE_TAG" test -f /app/config/lmcache-cpu-offload.yaml && echo "exists" || echo "not found")
check_result "LMCache config file exists" "$CONFIG_CHECK" "exists"

# Test 2: Verify config file contents
echo ""
echo "Test 2: Checking LMCache config file contents..."
CONFIG_CONTENT=$(docker run --rm "$IMAGE_TAG" cat /app/config/lmcache-cpu-offload.yaml)
check_contains "Config contains chunk_size: 8" "$CONFIG_CONTENT" "chunk_size: 8"
check_contains "Config contains local_cpu: true" "$CONFIG_CONTENT" "local_cpu: true"
check_contains "Config contains max_local_cpu_size: 5.0" "$CONFIG_CONTENT" "max_local_cpu_size: 5.0"

# Test 3: Verify environment variables are set
echo ""
echo "Test 3: Checking LMCache environment variables..."
# Combine all env var checks into a single docker run for efficiency
ENV_VARS=$(docker run --rm "$IMAGE_TAG" sh -c 'echo "LMCACHE_LOG_LEVEL=$LMCACHE_LOG_LEVEL"; echo "LMCACHE_CONFIG_FILE=$LMCACHE_CONFIG_FILE"; echo "LMCACHE_USE_EXPERIMENTAL=$LMCACHE_USE_EXPERIMENTAL"')

LMCACHE_LOG_LEVEL=$(echo "$ENV_VARS" | grep "LMCACHE_LOG_LEVEL=" | cut -d'=' -f2)
check_result "LMCACHE_LOG_LEVEL=WARNING" "$LMCACHE_LOG_LEVEL" "WARNING"

LMCACHE_CONFIG_FILE=$(echo "$ENV_VARS" | grep "LMCACHE_CONFIG_FILE=" | cut -d'=' -f2-)
check_result "LMCACHE_CONFIG_FILE=/app/config/lmcache-cpu-offload.yaml" "$LMCACHE_CONFIG_FILE" "/app/config/lmcache-cpu-offload.yaml"

LMCACHE_USE_EXPERIMENTAL=$(echo "$ENV_VARS" | grep "LMCACHE_USE_EXPERIMENTAL=" | cut -d'=' -f2)
check_result "LMCACHE_USE_EXPERIMENTAL=True" "$LMCACHE_USE_EXPERIMENTAL" "True"

# Test 4: Verify CMD arguments are set correctly
echo ""
echo "Test 4: Checking default CMD arguments..."
# Get the CMD from docker inspect
CMD_OUTPUT=$(docker inspect "$IMAGE_TAG" --format='{{json .Config.Cmd}}' 2>/dev/null || echo "[]")
check_contains "CMD contains --kv-transfer-config" "$CMD_OUTPUT" "kv-transfer-config"
check_contains "CMD contains LMCacheConnectorV1" "$CMD_OUTPUT" "LMCacheConnectorV1"
check_contains "CMD contains --no-enable-prefix-caching" "$CMD_OUTPUT" "no-enable-prefix-caching"
# Note: gpu-memory-utilization is not baked in, allowing runtime configuration

# Test 5: Verify ENTRYPOINT is vllm serve
echo ""
echo "Test 5: Checking ENTRYPOINT..."
ENTRYPOINT=$(docker inspect "$IMAGE_TAG" --format='{{json .Config.Entrypoint}}' 2>/dev/null || echo "[]")
check_contains "ENTRYPOINT contains vllm" "$ENTRYPOINT" "vllm"
check_contains "ENTRYPOINT contains serve" "$ENTRYPOINT" "serve"

# Test 6: Display full configuration for manual verification
echo ""
echo "=========================================="
echo "Configuration Summary"
echo "=========================================="
echo ""
echo "LMCache Config File Contents:"
echo "---"
docker run --rm "$IMAGE_TAG" cat /app/config/lmcache-cpu-offload.yaml
echo "---"
echo ""
echo "Environment Variables:"
echo "  LMCACHE_LOG_LEVEL=$LMCACHE_LOG_LEVEL"
echo "  LMCACHE_CONFIG_FILE=$LMCACHE_CONFIG_FILE"
echo "  LMCACHE_USE_EXPERIMENTAL=$LMCACHE_USE_EXPERIMENTAL"
echo ""
echo "Docker CMD:"
echo "  $CMD_OUTPUT"
echo ""
echo "Docker ENTRYPOINT:"
echo "  $ENTRYPOINT"
echo ""

# Summary
echo "=========================================="
echo "Test Results Summary"
echo "=========================================="
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"
    echo ""
    echo "Some tests failed. Please review the configuration."
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    echo ""
    echo "✓ LMCache configuration is correctly baked into the Docker image"
    exit 0
fi

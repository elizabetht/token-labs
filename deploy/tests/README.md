# Smoke Tests

This directory contains automated smoke tests to verify the cluster setup and core functionality.

## Overview

The tests validate:
1. Cluster node readiness
2. GPU access from containers
3. Inference through the AI Gateway
4. Token-based rate limiting

## Prerequisites

- `kubectl` configured to access the cluster
- `jq` for JSON parsing (tests 3 and 4)
- `curl` for HTTP requests (tests 3 and 4)

## Tests

### 1. Cluster Node Readiness

Verifies that all nodes in the cluster are in `Ready` state.

```bash
./01-test-cluster-nodes.sh
```

**Expected outcome**: All nodes show as `Ready`

---

### 2. GPU Access Test

Validates that GPU devices are accessible from containers.

```bash
./02-test-gpu-access.sh
```

**Expected outcome**: `nvidia-smi` runs successfully in a test pod and displays GPU information.

---

### 3. Inference Test

Tests model inference through the Envoy AI Gateway.

```bash
# With default API key
./03-test-inference.sh

# With custom API key
./03-test-inference.sh your-api-key-here
```

**Expected outcome**:
- HTTP 200 response
- Valid chat completion JSON with model response

---

### 4. Token Quota Test

Verifies token-based rate limiting by exhausting the quota.

```bash
# With defaults (test-api-key, 1000 token limit)
./04-test-token-quota.sh

# With custom API key and quota
./04-test-token-quota.sh your-api-key-here 5000
```

**Expected outcome**:
- Multiple successful requests (HTTP 200)
- Eventually receives HTTP 429 (Too Many Requests) when quota is exhausted

---

## Running All Tests

```bash
# Run all tests sequentially
for test in ./deploy/tests/*.sh; do
    echo "Running $test..."
    bash "$test" || echo "Test failed: $test"
    echo
done
```

## CI/CD Integration

These tests can be integrated into CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run smoke tests
  run: |
    cd deploy/tests
    ./01-test-cluster-nodes.sh
    ./02-test-gpu-access.sh
    ./03-test-inference.sh test-api-key
```

## Troubleshooting

### Test 1 fails: Nodes not Ready

Check node status and events:
```bash
kubectl get nodes
kubectl describe nodes
```

### Test 2 fails: GPU not accessible

Verify GPU operator and device plugin:
```bash
kubectl get pods -n gpu-operator
kubectl logs -n gpu-operator -l app=nvidia-device-plugin-daemonset
```

### Test 3 fails: Inference request fails

Check gateway and model services:
```bash
kubectl get gateway -n token-labs
kubectl get svc -n envoy-gateway-system
kubectl get pods -n token-labs
```

View gateway logs:
```bash
kubectl logs -n envoy-gateway-system -l gateway.envoyproxy.io/owning-gateway-name=ai-gateway
```

### Test 4 fails: Rate limiting not working

Check Limitador and policies:
```bash
kubectl get ratelimitpolicy -A
kubectl get tokenratelimitpolicy -A
kubectl logs -n kuadrant-system -l app=limitador
```

## Test Results

Each test script exits with:
- `0` on success (✅)
- `1` on failure (❌)

This allows easy integration with test frameworks and CI systems.

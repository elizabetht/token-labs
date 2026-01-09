# LMCache Configuration Tests

This directory contains functional tests to verify that LMCache configuration is properly baked into the v0.2.0 Docker image.

## Test Script: `test_lmcache_config.sh`

### Purpose
Verifies that the v0.2.0 Docker image has all LMCache configuration correctly baked in, including:
- LMCache config file presence and contents
- Environment variables (LMCACHE_LOG_LEVEL, LMCACHE_CONFIG_FILE, LMCACHE_USE_EXPERIMENTAL)
- Default CMD arguments (--kv-transfer-config, --no-enable-prefix-caching, --gpu-memory-utilization)
- ENTRYPOINT configuration

### Usage

```bash
# Test a local Docker image
./test_lmcache_config.sh <image-tag>

# Example: Test the v0.2.0 image
./test_lmcache_config.sh ghcr.io/elizabetht/token-labs/vllm-serve:v0.2.0

# Test a locally built image
docker build -t vllm-serve:test .
./test_lmcache_config.sh vllm-serve:test
```

### What It Tests

1. **Config File Presence**: Verifies `/app/config/lmcache-cpu-offload.yaml` exists
2. **Config File Contents**: Validates chunk_size=8, local_cpu=true, max_local_cpu_size=5.0
3. **Environment Variables**: Checks LMCACHE_LOG_LEVEL, LMCACHE_CONFIG_FILE, LMCACHE_USE_EXPERIMENTAL
4. **CMD Arguments**: Verifies --kv-transfer-config, --no-enable-prefix-caching (gpu-memory-utilization can be set at runtime)
5. **ENTRYPOINT**: Confirms vllm serve is the entry command

### Expected Output

When all tests pass:
```
==========================================
Testing LMCache Configuration in Docker Image
==========================================

Testing Docker image: vllm-serve:test

Test 1: Checking if LMCache config file exists...
✓ PASS: LMCache config file exists

Test 2: Checking LMCache config file contents...
✓ PASS: Config contains chunk_size: 8
✓ PASS: Config contains local_cpu: true
✓ PASS: Config contains max_local_cpu_size: 5.0

Test 3: Checking LMCache environment variables...
✓ PASS: LMCACHE_LOG_LEVEL=WARNING
✓ PASS: LMCACHE_CONFIG_FILE=/app/config/lmcache-cpu-offload.yaml
✓ PASS: LMCACHE_USE_EXPERIMENTAL=True

Test 4: Checking default CMD arguments...
✓ PASS: CMD contains --kv-transfer-config
✓ PASS: CMD contains LMCacheConnectorV1
✓ PASS: CMD contains --no-enable-prefix-caching

Test 5: Checking ENTRYPOINT...
✓ PASS: ENTRYPOINT contains vllm
✓ PASS: ENTRYPOINT contains serve

==========================================
Test Results Summary
==========================================
Passed: 12
All tests passed!

✓ LMCache configuration is correctly baked into the Docker image
```

### Integration with CI/CD

This test can be integrated into the GitHub Actions workflow after the Docker image is built:

```yaml
- name: Test LMCache Configuration
  run: |
    ./test_lmcache_config.sh ${{ steps.vars.outputs.FULL_IMAGE }}
```

### Exit Codes

- `0`: All tests passed
- `1`: One or more tests failed or image not found

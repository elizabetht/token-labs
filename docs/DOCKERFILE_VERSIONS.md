# Dockerfile Version Reference

This document describes the features and configurations available in each version of the Token Labs Dockerfile.

## Version Overview

Each tagged version of the Token Labs Dockerfile is built and tested against specific features and configurations. When running benchmarks or tests in the [token-labs-performance](https://github.com/elizabetht/token-labs-performance) repository, specify the appropriate Dockerfile version to ensure compatibility.

## Version History

### v0.3.0 (Latest)
**Features Enabled:**
- ✅ Prefix Caching
- ✅ Speculative Decoding (EAGLE3)
- ❌ LMCache

**Configuration:**
- GPU Memory Utilization: 0.6
- Speculative Model: `RedHatAI/Llama-3.1-8B-Instruct-speculator.eagle3`
- Speculative Tokens: 7

**Benchmark Tests:**
- Prefill benchmark (3072 input, 1024 output tokens)
- Decode benchmark (1024 input, 3072 output tokens)
- Cache benchmark (prefix_repetition dataset)

**Docker Image:**
```bash
ghcr.io/elizabetht/token-labs/vllm-serve:v0.3.0
```

### v0.2.0
**Features Enabled:**
- ❌ Prefix Caching
- ❌ Speculative Decoding
- ✅ LMCache (CPU offload)

**Configuration:**
- GPU Memory Utilization: 0.3
- LMCache chunk_size: 8
- LMCache local_cpu: true
- LMCache max_local_cpu_size: 5.0 GB

**Benchmark Tests:**
- Prefill benchmark (3072 input, 1024 output tokens)
- Decode benchmark (1024 input, 3072 output tokens)
- Cache benchmark (prefix_repetition dataset) - tests LMCache

**Docker Image:**
```bash
ghcr.io/elizabetht/token-labs/vllm-serve:v0.2.0
```

**Notes:**
- Uses `--kv-transfer-config` with LMCacheConnectorV1
- Requires `--no-enable-prefix-caching` flag
- LMCache configuration file mounted at `/app/config/lmcache-cpu-offload.yaml`

### v0.1.0
**Features Enabled:**
- ❌ Prefix Caching
- ❌ Speculative Decoding
- ❌ LMCache

**Configuration:**
- GPU Memory Utilization: 0.3
- Baseline vLLM configuration

**Benchmark Tests:**
- Prefill benchmark (3072 input, 1024 output tokens)
- Decode benchmark (1024 input, 3072 output tokens)
- Cache benchmark: SKIPPED (no prefix caching support)

**Docker Image:**
```bash
ghcr.io/elizabetht/token-labs/vllm-serve:v0.1.0
```

## Hardware Platform

All versions are built for:
- **Platform:** NVIDIA DGX Spark
- **GPU:** Grace Hopper
- **Architecture:** ARM64
- **CUDA:** 12.0 (Hopper architecture)

## Base Image Components

All versions include:
- **vLLM:** 0.12.0
- **LMCache:** 0.3.9
- **FlashInfer:** Latest compatible version
- **PyTorch:** CUDA 12.0 compatible

## Usage in token-labs-performance

When running benchmarks in the [token-labs-performance](https://github.com/elizabetht/token-labs-performance) repository, specify the Dockerfile version as an input parameter:

```yaml
# Example workflow input
image_tag: v0.2.0
```

This ensures that:
1. The correct Docker image is pulled
2. Feature-specific configurations are applied
3. Appropriate benchmark tests are executed
4. Results are tagged with the Dockerfile version

## Feature Testing Matrix

| Feature | v0.1.0 | v0.2.0 | v0.3.0 |
|---------|--------|--------|--------|
| Prefix Caching | ❌ | ❌ | ✅ |
| Speculative Decoding | ❌ | ❌ | ✅ |
| LMCache CPU Offload | ❌ | ✅ | ❌ |
| Baseline Performance | ✅ | ✅ | ✅ |

## Building New Versions

To build a new version:

1. Make changes to the Dockerfile
2. Commit and push to main branch
3. Create a new tag:
   ```bash
   git tag -a v0.4.0 -m "Release v0.4.0: Add feature X"
   git push origin v0.4.0
   ```
4. GitHub Actions will automatically build and push the image
5. Update this document with the new version details
6. Run benchmarks in token-labs-performance with the new version

## Related Documentation

- [README.md](../README.md) - Main repository documentation
- [token-labs-performance](https://github.com/elizabetht/token-labs-performance) - Benchmark and deployment scripts
- [Benchmark Results](https://elizabetht.github.io/token-labs/benchmark-results.html) - Live benchmark data

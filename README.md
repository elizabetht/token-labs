# Token Labs 🚀

[![Deploy and Benchmark](https://github.com/elizabetht/token-labs/actions/workflows/deploy-and-benchmark.yml/badge.svg)](https://github.com/elizabetht/token-labs/actions/workflows/deploy-and-benchmark.yml)
[![Build vLLM](https://github.com/elizabetht/token-labs/actions/workflows/build-and-push.yml/badge.svg?event=push)](https://github.com/elizabetht/token-labs/actions/workflows/build-and-push.yml)
[![Latest Release](https://img.shields.io/github/v/tag/elizabetht/token-labs?label=Latest%20Release)](https://github.com/elizabetht/token-labs/releases)

Self-hosted LLM inference on NVIDIA DGX Spark with automated benchmarking and cost analysis.

## 📊 Latest Benchmark Results

| Metric | Prefill (Input) | Decode (Output) |
|--------|-----------------|-----------------|
| Throughput | 3,203 tok/s | 520 tok/s |
| Cost/1M tokens | $0.006 | $0.037 |

👉 **[View Full Benchmark Results](https://elizabetht.github.io/token-labs/benchmark-results.html)**

👉 **[Raw JSON Data](https://elizabetht.github.io/token-labs/benchmark-results.json)**

## 🏗️ Architecture

- **Hardware**: NVIDIA DGX Spark (Grace Hopper, ARM64)
- **Inference Engine**: [vLLM](https://github.com/vllm-project/vllm)
- **Model**: Meta Llama 3.1 8B Instruct
- **CI/CD**: GitHub Actions with self-hosted runner

## 🚀 Docker Build Optimization

The Dockerfile is optimized for fast incremental builds using several caching strategies:

### Multi-Stage Build
- **Builder Stage**: Compiles vLLM and dependencies with all build tools
- **Runtime Stage**: Minimal image with only necessary runtime components (saves ~2GB)

### BuildKit Cache Mounts
The Dockerfile uses BuildKit's cache mount feature to persist:
- **APT cache** (`/var/cache/apt`, `/var/lib/apt/lists`): Speeds up package installation
- **pip cache** (`/root/.cache/pip`): Reuses downloaded Python packages across builds
- **Git cache** (`/root/.cache/git`): Speeds up git clone operations
- **Build artifacts** (`/app/vllm/build`, `/app/LMCache/build`): Caches compiled objects

### Layer Ordering
Layers are ordered from least to most frequently changing:
1. Base system packages (rarely changes)
2. Python environment setup (rarely changes)
3. PyTorch installation (rarely changes)
4. Other dependencies (occasionally changes)
5. vLLM/LMCache source (changes with version updates)

### GitHub Actions Cache
The build workflow uses GitHub Actions cache (`type=gha`) to persist layers between builds, enabling:
- **First build**: ~45 minutes (full compilation)
- **Subsequent builds** (no changes): ~2-5 minutes (cache hit)
- **Incremental builds** (dependency updates): ~10-15 minutes

The workflow automatically builds Docker images:
- **On push to main**: Creates image with `edge` tag
- **On pull requests**: Builds image for testing (cache only, no push)
- **On version tags** (`v*`): Creates image with version tag and `latest` tag

### Version Pinning
To maximize cache reuse, the Dockerfile supports build arguments:
- `VLLM_COMMIT`: Pin vLLM to a specific git commit (default: `main`)
- `LMCACHE_COMMIT`: Pin LMCache to a specific commit (default: `main`)
- `CUDA_ARCH`: Target CUDA architecture for compilation (default: `12.0f` for DGX Spark/H100)
  - Note: The default `12.0f` is vLLM-specific notation for Grace Hopper architecture
  - For other GPUs, use standard compute capability values (e.g., `8.0` for A100, `9.0` for H100)

Example of building with pinned versions:
```bash
docker build \
  --build-arg VLLM_COMMIT=v0.6.5 \
  --build-arg LMCACHE_COMMIT=abc1234 \
  --build-arg CUDA_ARCH=8.0 \
  -t vllm-serve:custom .
```

### Cache Maintenance
- Caches are automatically managed by GitHub Actions (7-day retention)
- To force a clean rebuild, update the `VLLM_COMMIT` or `LMCACHE_COMMIT` build args
- BuildKit cache mounts persist across builds on the same runner

## 💰 Cost Economics

DGX Spark running costs:
- Hardware amortization: ~$0.05/hr ($4000 over 3 years @ 30% utilization)
- Electricity: ~$0.02/hr
- **Total: ~$0.07/hour**

## 🔗 Links

- [Live Demo](https://elizabetht.github.io/token-labs/)
- [Benchmark Results](https://elizabetht.github.io/token-labs/benchmark-results.html)
- [GitHub Actions](https://github.com/elizabetht/token-labs/actions)

## 📁 Repository Structure

```
├── Dockerfile              # vLLM build for ARM64/CUDA 13.0
├── docs/
│   ├── index.html          # Main landing page
│   ├── benchmark-results.html  # Detailed benchmark results
│   └── benchmark-results.json  # Raw JSON data (auto-updated)
├── scripts/
│   └── update_pricing.py   # Updates pricing in docs
└── .github/workflows/
    ├── build-and-push.yml      # Build vLLM Docker image
    └── deploy-and-benchmark.yml # Deploy and run benchmarks
```

## License

MIT

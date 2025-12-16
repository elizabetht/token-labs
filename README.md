# Token Labs 🚀

[![Build vLLM](https://github.com/elizabetht/token-labs/actions/workflows/build-and-push.yml/badge.svg?event=push)](https://github.com/elizabetht/token-labs/actions/workflows/build-and-push.yml)
[![Latest Release](https://img.shields.io/github/v/tag/elizabetht/token-labs?label=Latest%20Release)](https://github.com/elizabetht/token-labs/releases)

Self-hosted LLM inference on NVIDIA DGX Spark with automated benchmarking and cost analysis.

> **Note:** Deployment and benchmark scripts have been migrated to the [token-labs-performance](https://github.com/elizabetht/token-labs-performance) repository. This repository now focuses on maintaining the vLLM Dockerfile and serving infrastructure.

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
├── Dockerfile                    # vLLM build for ARM64/CUDA 12.0
├── entrypoint.sh                # Docker container entrypoint
├── config/
│   └── lmcache-cpu-offload.yaml # LMCache configuration
├── docs/
│   ├── index.html               # Main landing page
│   ├── benchmark-results.html   # Detailed benchmark results
│   ├── benchmark-results.json   # Raw JSON data (auto-updated)
│   └── DOCKERFILE_VERSIONS.md   # Dockerfile version documentation
└── .github/workflows/
    └── build-and-push.yml       # Build vLLM Docker image
```

## 🔧 Deployment & Benchmarking

Deployment and benchmark scripts are maintained in a separate repository:

**[token-labs-performance](https://github.com/elizabetht/token-labs-performance)**

This separation allows:
- Independent versioning of deployment scripts and Docker images
- Testing different Dockerfile versions against various benchmarks
- Cleaner separation of concerns (infrastructure vs. testing)

### Dockerfile Versions

Each tagged version of the Dockerfile supports different feature sets. See [docs/DOCKERFILE_VERSIONS.md](docs/DOCKERFILE_VERSIONS.md) for details on:
- Feature availability (LMCache, Prefix Caching, Speculative Decoding)
- Configuration parameters
- Benchmark test specifications
- Version compatibility matrix

### Running Benchmarks

To run benchmarks against a specific Dockerfile version:

1. Navigate to [token-labs-performance](https://github.com/elizabetht/token-labs-performance)
2. Trigger the deployment workflow with the desired `image_tag` (e.g., `v0.2.0`)
3. Results will be automatically published to [www.tokenlabs.run/benchmark-results.html](https://elizabetht.github.io/token-labs/benchmark-results.html)

## License

MIT

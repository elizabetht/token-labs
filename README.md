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
- **Cache**: [LMCache](https://github.com/LMCache/LMCache) v0.3.9 with CPU offloading

### Version 0.2.0 Features

v0.2.0 includes **baked-in LMCache configuration** for optimal prefix caching performance:

- **Chunk Size**: 8 tokens
- **CPU Offloading**: Enabled with 5.0 GB max cache
- **KV Transfer**: Bidirectional with LMCacheConnectorV1

All LMCache settings are pre-configured in the Docker image - no runtime environment variables needed. GPU memory utilization can be configured at runtime as needed.

### Testing LMCache Configuration

To verify the LMCache configuration is correctly baked into the Docker image:

```bash
./test_lmcache_config.sh <image-tag>
```

See [tests/README.md](tests/README.md) for detailed test documentation.

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
├── Dockerfile              # vLLM build for ARM64/CUDA 13.0 with LMCache
├── config/
│   └── lmcache-cpu-offload.yaml  # LMCache config (baked into v0.2.0)
├── docs/
│   ├── index.html          # Main landing page
│   ├── benchmark-results.html  # Detailed benchmark results
│   └── benchmark-results.json  # Raw JSON data (auto-updated)
├── scripts/
│   ├── generate_results.py # Generate benchmark JSON (v0.2.0 LMCache hardcoded)
│   └── update_pricing.py   # Updates pricing in docs
├── tests/
│   └── README.md           # Test documentation
├── test_lmcache_config.sh  # Functional test for LMCache configuration
└── .github/workflows/
    ├── build-and-push.yml      # Build vLLM Docker image
    └── deploy-and-benchmark.yml # Deploy and run benchmarks
```

## License

MIT

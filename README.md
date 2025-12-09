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

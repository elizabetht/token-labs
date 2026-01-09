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

## 🎯 Accuracy Testing

Token Labs includes automated accuracy testing using the [IFEval benchmark](https://github.com/oKatanaaa/ifeval) to ensure model quality is maintained across different configurations and quantizations.

### Baseline Comparison

The workflow automatically compares model accuracy against established baselines:

**Baseline Model**: `meta-llama/Llama-3.1-8B-Instruct` (unquantized)
- Establishes reference accuracy for instruction-following capability
- Baseline values are auto-updated when running the baseline model
- See [`baselines/`](baselines/) for baseline configurations

**Quantized Models**:
- `tokenlabsdotrun/Llama-3.1-8B-ModelOpt-NVFP4` - FP4 quantized variant
- `tokenlabsdotrun/Llama-3.1-8B-ModelOpt-FP8` - FP8 quantized variant

### Running Comparisons

1. **Establish Baseline** (first time or to update):
   ```bash
   # Via GitHub Actions UI
   # Select model: meta-llama/Llama-3.1-8B-Instruct
   # This will update the baseline values
   ```

2. **Compare Quantized Model**:
   ```bash
   # Via GitHub Actions UI
   # Select model: tokenlabsdotrun/Llama-3.1-8B-ModelOpt-NVFP4
   # Workflow will automatically compare against baseline
   ```

### Comparison Thresholds

Models are compared using ±5% accuracy threshold on IFEval metrics:
- ✅ **PASS**: Accuracy within 5% of baseline
- ❌ **FAIL**: Accuracy degraded >5% from baseline
- 🎉 **IMPROVED**: Accuracy improved beyond baseline

See [`baselines/README.md`](baselines/README.md) for detailed documentation.

## 📁 Repository Structure

```
├── Dockerfile              # vLLM build for ARM64/CUDA 13.0
├── baselines/              # Baseline accuracy values for comparison
│   ├── README.md           # Documentation for baseline testing
│   └── llama-3.1-8b-instruct.json  # Baseline for Llama 3.1 8B
├── docs/
│   ├── index.html          # Main landing page
│   ├── benchmark-results.html  # Detailed benchmark results
│   └── benchmark-results.json  # Raw JSON data (auto-updated)
├── scripts/
│   ├── compare_baseline.py # Compare accuracy against baseline
│   ├── evaluate_accuracy.py # Run IFEval accuracy evaluation
│   ├── generate_results.py # Generate benchmark result files
│   └── update_pricing.py   # Updates pricing in docs
└── .github/workflows/
    ├── build-and-push.yml      # Build vLLM Docker image
    └── deploy-and-benchmark.yml # Deploy and run benchmarks
```

## License

MIT

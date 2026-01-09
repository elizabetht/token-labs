# Baseline Accuracy Values

This directory contains baseline accuracy values for models to enable comparison and regression detection.

## Structure

Each baseline file contains:
- Model identification
- IFEval accuracy metrics (strict and loose)
- Performance metrics (throughput)
- Metadata (last updated, run ID)

## Usage

### Establishing a Baseline

To establish a baseline for a model, run the deploy-and-benchmark workflow with the baseline model:

```bash
# Via GitHub Actions UI:
# 1. Go to Actions > Deploy and Benchmark on DGX Spark
# 2. Click "Run workflow"
# 3. Select model: "meta-llama/Llama-3.1-8B-Instruct"
# 4. Run the workflow
```

The workflow will automatically update the baseline file with the results.

### Comparing Against Baseline

When benchmarking a different model (e.g., a quantized version), the workflow will automatically compare against the baseline:

```bash
# Via GitHub Actions UI:
# 1. Go to Actions > Deploy and Benchmark on DGX Spark
# 2. Click "Run workflow"
# 3. Select model: "tokenlabsdotrun/Llama-3.1-8B-ModelOpt-NVFP4"
# 4. Run the workflow
```

The comparison results will show:
- ✅ PASS: Model accuracy within acceptable threshold (±5%)
- ❌ FAIL: Model accuracy degraded beyond threshold
- 🎉 IMPROVED: Model accuracy improved

### Manual Comparison

You can also manually compare results using the comparison script:

```bash
# Compare new results against baseline
python scripts/compare_baseline.py \
  --results ifeval_results.json \
  --baseline baselines/llama-3.1-8b-instruct.json

# Update baseline with new results
python scripts/compare_baseline.py \
  --results ifeval_results.json \
  --baseline baselines/llama-3.1-8b-instruct.json \
  --update-baseline \
  --run-id "12345"
```

## Baseline Files

### llama-3.1-8b-instruct.json

Baseline for the unquantized Llama 3.1 8B Instruct model. This serves as the reference for comparing quantized versions.

**Model:** `meta-llama/Llama-3.1-8B-Instruct`

Expected accuracy range:
- Prompt-level accuracy (strict): TBD after first successful run
- Instruction-level accuracy (strict): TBD after first successful run

## Acceptable Thresholds

The comparison script uses the following thresholds for determining pass/fail:
- **Prompt-level accuracy:** ±5% from baseline
- **Instruction-level accuracy:** ±5% from baseline

These thresholds ensure that quantization or optimization doesn't significantly degrade the model's instruction-following capability.

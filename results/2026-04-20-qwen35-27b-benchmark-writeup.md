---
day: 25b
date: 2026-04-20
slug: qwen35-27b-vllm-sglang-gptq-int4-dgx-spark-benchmark
format: LinkedIn
status: ready
published_url:
---

Ran a full throughput and latency sweep of Qwen3.5-27B on a single DGX Spark GB10 (128GB unified memory, SM 12.1). GPTQ-Int4, vllm vs sglang, 9 optimization techniques, synthetic and real (ShareGPT) workloads. Here's what actually moved the numbers.

---

**Setup**

- Model: Qwen/Qwen3.5-27B-GPTQ-Int4
- Hardware: NVIDIA DGX Spark GB10, 128GB unified memory, SM 12.1
- Frameworks: vllm (cu130-nightly), sglang (nightly)
- Quantization: GPTQ-Int4 (gptq_marlin kernel)
- Synthetic combos: ISL1024/OSL1024, ISL4096/OSL1024, ISL1024/OSL4096
- Real workload: ShareGPT (641MB, avg ~1500 input / ~500 output tokens)
- Concurrency: 1, 8, 32
- All runs with APC + chunked prefill enabled (baseline)

**Framework winner: vllm for throughput, sglang for latency**

At peak throughput (c=32, ISL1024/OSL1024), vllm hit 109 tok/s vs sglang at 103 tok/s. sglang is faster on first-token latency at c=1: 189ms TTFT vs vllm's 273ms — a 30% advantage that matters for interactive use cases. If you're building a chatbot, sglang. If you're doing batch inference, vllm.

**The central finding: GB10 is compute-saturated at INT4**

Ran 9 vllm techniques beyond the APC/chunked-prefill baseline:

| Technique | Peak tok/s (c=32) | vs baseline |
|-----------|-------------------|-------------|
| spec-ngram | **111.0** | +1.6% |
| baseline | 109.3 | — |
| kv-fp8 | 108.3 | -0.9% |
| spec-mtp | 107.9 | -1.3% |
| lmcache-8g | 106.5 | -2.6% |
| lmcache-20g | 106.1 | -2.9% |
| no-cuda-graph | 108.7* | synthetic only |

*no-cuda-graph on synthetic ISL1024/OSL1024; on ShareGPT real workload it's flat with baseline at c=1.

Every technique is within 3% of baseline. The GB10's INT4 matrix units are the bottleneck — KV cache is small relative to 128GB, memory bandwidth isn't the constraint, and the scheduler has nothing to optimize around because compute is the only limiter.

**What actually helped: spec-ngram (+1.6%)**

N-gram speculation on ShareGPT gets a small but consistent gain across all concurrencies:

| | c=1 | c=8 | c=32 |
|--|-----|-----|------|
| baseline | 8.14 | 47.07 | 109.33 |
| spec-ngram | 8.21 | 48.55 | 111.05 |

The acceptance rate is higher on real conversations than synthetic random data — real outputs repeat phrases and patterns, which n-gram drafts can predict. Still marginal on a compute-bound system.

**What didn't help: kv-fp8, lmcache, spec-mtp**

kv-fp8 halves KV cache memory usage — useful when you're KV-cache-bound. On a 128GB system running a 13.5GB model with 16K context, you're not. The benefit shows up on multi-GPU deployments where KV cache spills to slower memory.

lmcache adds a CPU-side KV cache layer for cross-request prefix reuse. Adds overhead, doesn't recoup it when the GPU isn't waiting on memory.

spec-mtp uses the model's own MTP heads for drafting — higher acceptance rate than n-gram in theory, but the verification pass costs more than the speculation saves on GB10.

**Real workload reveals batching dynamics**

ShareGPT at c=1: 8.14 tok/s. At c=32: 109.33 tok/s. That's a 13.4x gain — nearly linear scaling. APC is the reason: concurrent requests sharing common system prompts amortize prefill cost across the batch. Synthetic random data shows similar scaling (~13x) but for different reasons — pure arithmetic batching.

The implication: if your workload has any shared context structure, maximize concurrency before reaching for quantization or speculative techniques.

**CUDA graphs matter at low concurrency**

no-cuda-graph on ShareGPT c=1: 8.15 tok/s, same as baseline. At c=1 with long contexts, the per-token decode dominates and graph overhead disappears in the noise. At high concurrency with short decode steps, CUDA graphs provide measurable latency reduction (the per-step kernel launch overhead accumulates). The effect is hardware-specific — on GB10 with unified memory, the savings are smaller than on discrete GPU.

**Numbers**

All results at: github.com/elizabetht/token-labs/results

Full Phase B (framework + quantization sweep), Phase C (combo techniques: kv-fp8+lmcache, kv-fp8+spec), Phase D (ShareGPT sweep across 5 techniques × 3 concurrencies).

---

The answer to "which vllm optimization should I use on a single DGX Spark GB10?" is: spec-ngram for a free +1.6%, everything else is noise. The real lever is concurrency — get to c=32 before tuning anything else.

---

**Phase E (planned): speculative decoding at low concurrency**

All Phase D spec decoding runs were at c=1/8/32 against a compute-saturated system. The untested regime is c=1–4 where the GB10 is memory-bandwidth-bound and speculative decoding has real upside. Planned techniques:

| Variant | Draft source | Expected gain regime |
|---|---|---|
| spec-draft (small model) | Qwen2.5-1.5B or 3B | c=1–4, latency-focused |
| Eagle | Lightweight head trained on Qwen3.5-27B activations | c=1–8 |
| Medusa | Multi-head parallel drafts, no separate model | c=1–4 |
| spec-ngram (baseline) | n-gram prompt lookup | all concurrencies (already done) |

Hypothesis: at c=1 on ShareGPT, a draft model (1.5B–3B) could push TTFT below 150ms and decode latency toward 2–3x speedup. Eagle and Medusa avoid the memory overhead of a second model but require fine-tuning heads on the target model. Worth quantifying before concluding spec decoding is ineffective on GB10.

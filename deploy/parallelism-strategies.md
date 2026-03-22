# LLM Inference Parallelism: DP, TP, PP, EP, and Wide-EP Explained

*A practical guide to how large language models are distributed across GPUs for inference — what each strategy does, when to use it, and how they combine.*

---

## Why Parallelism Matters for Inference

A single GPU has two hard limits: **memory** (how large a model it can hold) and **compute** (how fast it can process tokens). When a model exceeds either limit, the work must be split across multiple GPUs.

The way you split that work determines your latency, throughput, hardware requirements, and cost. There is no single best strategy — each makes a different tradeoff. Understanding these tradeoffs is essential for anyone deploying LLMs in production.

---

## Data Parallelism (DP)

**What it does:** Replicates the entire model on every GPU. Each replica handles different requests independently.

```
Request A ──▶ [ GPU 0: Full Model ] ──▶ Response A
Request B ──▶ [ GPU 1: Full Model ] ──▶ Response B
Request C ──▶ [ GPU 2: Full Model ] ──▶ Response C
Request D ──▶ [ GPU 3: Full Model ] ──▶ Response D
```

**How it works:**
- Each GPU loads a complete copy of the model weights
- A load balancer distributes incoming requests across replicas
- No communication between GPUs during inference — each request is fully independent

**Communication pattern:** None between GPUs. Coordination happens at the routing/load-balancer level.

**Tradeoffs:**

| Advantage | Disadvantage |
|-----------|-------------|
| Simplest to implement | Highest memory usage (full model per GPU) |
| Linear throughput scaling | Single-request latency is unchanged |
| Zero inter-GPU communication | Model must fit on one GPU |

**When to use:** The model fits on a single GPU and you need to handle more concurrent requests. This is the default strategy for most production deployments — scale horizontally by adding replicas.

**Example:** Serving Llama 3.1 8B on 4 GPUs. Each GPU holds the full model. 4x the throughput, same per-request latency.

---

## Tensor Parallelism (TP)

**What it does:** Splits individual layers (matrix multiplications) across GPUs. Every GPU computes a slice of every layer.

```
                    ┌─── GPU 0: Slice 0 of each layer ───┐
                    │                                      │
Request ──▶ Split ──┼─── GPU 1: Slice 1 of each layer ───┼── All-Reduce ──▶ Response
                    │                                      │
                    ├─── GPU 2: Slice 2 of each layer ───┤
                    │                                      │
                    └─── GPU 3: Slice 3 of each layer ───┘
```

**How it works:**
- Linear layers are sliced along the hidden dimension — if a layer has a 4096x4096 weight matrix and TP=4, each GPU holds a 4096x1024 slice
- Attention heads are split across GPUs (e.g., 32 heads across 4 GPUs = 8 heads per GPU)
- After each layer, an **all-reduce** operation synchronizes results across all GPUs
- Every GPU participates in every token's computation

**Communication pattern:** All-reduce after every layer. This is the heaviest communication pattern of any strategy, demanding fast interconnects like NVLink (900 GB/s) or InfiniBand. Ethernet is too slow.

**Tradeoffs:**

| Advantage | Disadvantage |
|-----------|-------------|
| Reduces per-request latency | All-reduce every layer = heavy communication |
| Enables models larger than one GPU's memory | Requires NVLink or InfiniBand |
| Best TTFT improvement | Diminishing returns beyond 8 GPUs |

**When to use:** The model doesn't fit on one GPU, and latency matters (interactive use cases, chat, real-time APIs). TP is the standard approach for single-node multi-GPU serving.

**Example:** Llama 3.1 70B with TP=8 on a single 8xH100 node. Each GPU holds 1/8 of every layer. ~7x faster TTFT than running on a single (hypothetical) GPU.

---

## Pipeline Parallelism (PP)

**What it does:** Splits model layers sequentially across GPUs. Each GPU owns a contiguous block of layers (a "stage"), and activations flow through stages like an assembly line.

```
Request ──▶ [ GPU 0: Layers 0-19 ] ──▶ [ GPU 1: Layers 20-39 ] ──▶ [ GPU 2: Layers 40-59 ] ──▶ [ GPU 3: Layers 60-79 ] ──▶ Response
```

**How it works:**
- The model's layers are divided into contiguous groups, each assigned to a different GPU
- GPU 0 processes layers 0-19, sends the activation tensor to GPU 1, which processes layers 20-39, and so on
- Microbatching: multiple requests overlap in the pipeline so that all GPUs stay busy
- Each inter-stage transfer is a single point-to-point send — much lighter than TP's all-reduce

**Communication pattern:** Point-to-point activation transfer between adjacent stages. Works over ethernet — no NVLink required. This makes PP the only viable strategy for splitting across network-connected nodes.

**Tradeoffs:**

| Advantage | Disadvantage |
|-----------|-------------|
| Works over ethernet (no NVLink needed) | Pipeline bubbles — GPUs idle waiting for upstream stages |
| Low communication overhead per stage | Higher latency for individual requests |
| Handles very deep models | Requires careful stage balancing |

**When to use:** The model doesn't fit on one GPU, GPUs are connected via ethernet (multi-node), and you can tolerate higher per-request latency. PP is also useful combined with TP within nodes.

**Example:** A 120GB model across 2 DGX Sparks connected via ethernet. Node 1 runs layers 0-39, Node 2 runs layers 40-79. Each request crosses the network once per layer boundary.

---

## Expert Parallelism (EP)

**What it does:** Distributes the experts in a Mixture-of-Experts (MoE) model across GPUs. Each GPU holds a subset of experts and only processes the tokens routed to its experts.

```
                              ┌─ GPU 0: Experts 0-7   (processes tokens routed here)
                              │
Request ──▶ Attention (all) ──┤─ GPU 1: Experts 8-15  (processes tokens routed here)
            Router decides     │
            top-k experts ────┤─ GPU 2: Experts 16-23 (processes tokens routed here)
                              │
                              └─ GPU 3: Experts 24-31 (processes tokens routed here)
```

**How it works:**
- MoE models have a **router** that assigns each token to its top-k experts (typically k=2 out of 64-256 experts)
- Each GPU holds the weights for a subset of experts
- After the attention computation (which every GPU does), tokens are **dispatched** to the GPUs that hold their assigned experts
- After expert computation, results are **combined** back
- Dispatch and combine are **sparse** — a token only communicates with the 2 GPUs hosting its experts, not all GPUs

**Communication pattern:** All-to-all for dispatch/combine, but sparse. Far less data than TP's all-reduce since each token only touches k out of N GPUs.

**Tradeoffs:**

| Advantage | Disadvantage |
|-----------|-------------|
| Scales to massive parameter counts via sparsity | Only works for MoE architectures |
| Higher performance-per-parameter than dense models | Load imbalance if some experts are "hotter" |
| Sparse communication scales better than TP | Routing overhead adds latency at low concurrency |

**When to use:** You're serving an MoE model (DeepSeek, Mixtral, Qwen MoE) and need to distribute experts across GPUs. Often combined with TP for the attention layers.

**Example:** DeepSeek-R1 with 256 experts, EP=8. Each GPU holds 32 experts. A token activating experts 5 and 130 only talks to GPU 0 and GPU 4.

---

## Wide Expert Parallelism (Wide-EP)

**What it does:** Scales EP across many nodes by combining data-parallel attention with expert-parallel MoE layers, using prefill/decode disaggregation to optimize each phase independently.

```
                    PREFILL WORKERS (DP=16)                     DECODE WORKERS (DP=16)
              ┌──────────────────────────────┐            ┌──────────────────────────────┐
              │  16 GPUs, each runs full      │            │  16 GPUs, each runs full      │
              │  attention independently       │   NIXL    │  attention independently       │
              │                                │ ───────▶  │                                │
              │  Experts split across all 16   │ KV cache  │  Experts split across all 16   │
              │  DeepEP dispatch (sparse)      │ transfer  │  DeepEP low-latency dispatch   │
              └──────────────────────────────┘            └──────────────────────────────┘
```

**How it works:**

Wide-EP is a combination of three techniques:

1. **Data-parallel attention:** Each GPU independently runs the full attention computation on its own batch of tokens. No communication needed for attention — this is pure DP.

2. **Expert-parallel MoE layers:** Experts are distributed across all GPUs in the group. After attention, DeepEP's dispatch kernels send each token to its assigned expert GPUs using sparse all-to-all communication over RDMA.

3. **Prefill/decode disaggregation:** Prefill (compute-heavy) and decode (memory-bandwidth-heavy) run on separate GPU groups. NVIDIA's NIXL library transfers KV cache from prefill workers to decode workers over RDMA. Each group uses different kernel optimizations:
   - Prefill: high-throughput DeepEP dispatch
   - Decode: low-latency DeepEP dispatch

**Additional components:**
- **LeaderWorkerSet (LWS):** Kubernetes controller that manages multi-node pod groups as a single unit. One leader pod coordinates worker pods that form a distributed vLLM instance.
- **EPLB (Expert-Parallel Load Balancer):** Periodically rebalances expert placement and replicates heavily-used experts to handle uneven token distribution.

**Communication pattern:** Sparse all-to-all over InfiniBand/RoCE RDMA for expert dispatch. Point-to-point RDMA for KV cache transfer. Requires full-mesh network connectivity — every NIC must reach every other NIC.

**Tradeoffs:**

| Advantage | Disadvantage |
|-----------|-------------|
| Scales to 32-96+ GPUs across nodes | Requires InfiniBand RDMA (full mesh) |
| ~3,100 output tok/s per GPU on H200 | Only works for MoE models |
| Sparse dispatch scales better than TP | Complex orchestration (LWS, NIXL, DeepEP) |
| Prefill/decode separation optimizes both phases | Minimum 24-32 H200/B200 GPUs |

**When to use:** You're serving a large MoE model (DeepSeek-R1) at scale, you have InfiniBand-connected GPU clusters, and you need production throughput. This is the current state-of-the-art for MoE inference.

**Example:** DeepSeek-R1-0528 on 32 H200 GPUs with llm-d: 16 GPUs for prefill (DP=16), 16 for decode (DP=16). Achieves ~3,200 input tok/s/GPU and ~3,100 output tok/s/GPU.

---

## Comparison at a Glance

| Strategy | Splits | Communication | Interconnect | Latency Impact | Throughput Impact | Model Type |
|----------|--------|---------------|-------------|----------------|-------------------|------------|
| **DP** | Nothing (replicate) | None | Any | No change | Linear scaling | Any |
| **TP** | Layers (horizontal) | All-reduce every layer | NVLink / IB | Reduces TTFT | Moderate | Any |
| **PP** | Layers (vertical) | Point-to-point per stage | Ethernet OK | Increases | Moderate | Any |
| **EP** | Experts | Sparse all-to-all | NVLink / IB | Variable | High for MoE | MoE only |
| **Wide-EP** | Experts + PD disagg | Sparse all-to-all + NIXL | IB RDMA only | Optimized per phase | Highest | MoE only |

---

## How They Combine

In practice, production deployments mix strategies:

| Combination | Use Case | Example |
|-------------|----------|---------|
| **TP + DP** | Most common inference setup | 8xH100 node with TP=8, scale out with DP replicas |
| **TP + PP** | Very large dense models across nodes | TP=8 within each node, PP=2 across nodes for 400B+ models |
| **EP + DP** | MoE at scale (Wide-EP) | EP across GPUs for experts, DP for attention |
| **TP + EP** | MoE on a single node | TP for attention, EP for expert layers |

The emerging direction is **N-D parallelism** — combining context parallelism, pipeline parallelism, expert parallelism, and tensor parallelism across different dimensions of the cluster, with disaggregated prefill and decode tiers running on hardware optimized for each phase.

---

## Decision Flowchart

```
Does the model fit on one GPU?
├── Yes → Use DP (add replicas for throughput)
└── No
    ├── Dense model?
    │   ├── GPUs connected via NVLink? → Use TP
    │   └── GPUs on separate nodes (ethernet)? → Use PP (or quantize to fit one GPU)
    └── MoE model?
        ├── Single node, multiple GPUs? → Use EP (+ TP for attention)
        └── Multi-node with InfiniBand? → Use Wide-EP with PD disaggregation
```

---

## References

- [LLM Inference Parallelism Strategies (Wilson Wu)](https://wilsonwu.me/en/blog/2025/llm-inference-parallelism-in-vllm/) — Detailed vLLM performance verification of TP, DP, PP, EP
- [Scaling LLM Inference (Meta Engineering)](https://engineering.fb.com/2025/10/17/ai-research/scaling-llm-inference-innovations-tensor-parallelism-context-parallelism-expert-parallelism/) — Meta's innovations in TP, CP, and EP
- [vLLM Large Scale Serving: DeepSeek with Wide-EP](https://vllm.ai/blog/large-scale-serving) — Production benchmarks at 2.2k tok/s per H200
- [Scaling DeepSeek MoEs with vLLM and llm-d (Red Hat)](https://developers.redhat.com/articles/2025/09/08/scaling-deepseek-style-moes-vllm-and-llm-d-using-wide-ep) — llm-d Wide-EP architecture
- [llm-d Wide-EP with LeaderWorkerSet Guide](https://llm-d.ai/docs/guide/Installation/wide-ep-lws) — Deployment guide for Wide-EP on Kubernetes
- [Data, Tensor, Pipeline, Expert Parallelism (BentoML)](https://bentoml.com/llm/inference-optimization/data-tensor-pipeline-expert-hybrid-parallelism) — Comprehensive parallelism overview
- [vLLM Parallelism and Scaling Docs](https://docs.vllm.ai/en/stable/serving/parallelism_scaling/) — Official vLLM configuration reference

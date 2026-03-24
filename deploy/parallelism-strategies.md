# LLM Inference Parallelism: DP, TP, PP, EP, and Wide-EP

Every GPU has two hard walls: memory (how large a model it can hold) and compute (how fast it generates tokens). When a model exceeds either, work gets split across multiple GPUs. How you split it determines latency, throughput, hardware requirements, and cost. There is no single best strategy. Each trades something for something else.

---

## Data Parallelism (DP)

The simplest strategy. Copy the entire model onto every GPU. Each replica handles different requests independently.

```
Request A ──▶ [ GPU 0: Full Model ] ──▶ Response A
Request B ──▶ [ GPU 1: Full Model ] ──▶ Response B
Request C ──▶ [ GPU 2: Full Model ] ──▶ Response C
Request D ──▶ [ GPU 3: Full Model ] ──▶ Response D
```

Each GPU loads a complete copy of model weights. A load balancer distributes incoming requests. Zero inter-GPU communication during inference — every request runs in isolation.

Throughput scales linearly. Per-request latency doesn't change at all. The catch: the model has to fit on a single GPU, and memory usage is maximized because every GPU holds the full weights.

This is the default for most production deployments. Llama 3.1 8B on 4 GPUs means 4 replicas, 4x throughput, same latency. If the model fits, start here.

---

## Tensor Parallelism (TP)

TP slices individual layers across GPUs. Every GPU computes a piece of every layer, every token.

```
                    ┌─── GPU 0: Slice 0 of each layer ───┐
                    │                                      │
Request ──▶ Split ──┼─── GPU 1: Slice 1 of each layer ───┼── All-Reduce ──▶ Response
                    │                                      │
                    ├─── GPU 2: Slice 2 of each layer ───┤
                    │                                      │
                    └─── GPU 3: Slice 3 of each layer ───┘
```

A 4096x4096 weight matrix with TP=4 becomes four 4096x1024 slices, one per GPU. Attention heads split too — 32 heads across 4 GPUs = 8 heads per GPU. After every layer, an all-reduce synchronizes results across all GPUs.

That all-reduce is the constraint. It happens every single layer, which means TP has the heaviest communication pattern of any strategy. This demands NVLink (900 GB/s) or InfiniBand. Ethernet is too slow — the all-reduce latency kills throughput. Returns also diminish fast beyond 8 GPUs because synchronization overhead grows while per-GPU compute shrinks.

TP is the standard approach for serving models that don't fit on one GPU when latency matters. Llama 3.1 70B with TP=8 on a single 8xH100 node: each GPU holds 1/8 of every layer, TTFT improves roughly 7x versus a hypothetical single-GPU run.

---

## Pipeline Parallelism (PP)

PP cuts the model vertically — each GPU gets a contiguous block of layers and activations flow through like an assembly line.

```
Request ──▶ [ GPU 0: Layers 0-19 ] ──▶ [ GPU 1: Layers 20-39 ] ──▶ [ GPU 2: Layers 40-59 ] ──▶ [ GPU 3: Layers 60-79 ] ──▶ Response
```

GPU 0 processes layers 0-19, sends one activation tensor to GPU 1, which processes 20-39, and so on. Each inter-stage transfer is a single point-to-point send — vastly lighter than TP's all-reduce. Microbatching overlaps multiple requests in the pipeline so GPUs stay busy.

The key advantage: PP works over ethernet. No NVLink required. This makes it the only viable strategy for splitting a model across network-connected nodes.

The key disadvantage: pipeline bubbles. GPUs sit idle waiting for upstream stages to finish. Individual request latency is higher than TP because tokens traverse the full pipeline sequentially. Stage balancing also matters — unequal layer distribution means the slowest stage bottlenecks everything.

A 120GB model across 2 DGX Sparks on ethernet: Node 1 runs layers 0-39, Node 2 runs layers 40-79. Each request crosses the network once per stage boundary.

---

## Expert Parallelism (EP)

EP distributes the experts in a Mixture-of-Experts model across GPUs. Only relevant for MoE architectures — DeepSeek, Mixtral, Qwen MoE.

```
                              ┌─ GPU 0: Experts 0-7   (processes tokens routed here)
                              │
Request ──▶ Attention (all) ──┤─ GPU 1: Experts 8-15  (processes tokens routed here)
            Router decides     │
            top-k experts ────┤─ GPU 2: Experts 16-23 (processes tokens routed here)
                              │
                              └─ GPU 3: Experts 24-31 (processes tokens routed here)
```

MoE models have a router that assigns each token to its top-k experts (typically k=2 out of 64-256 total). Each GPU holds a subset of expert weights. After attention (computed on all GPUs), tokens get dispatched to whichever GPU holds their assigned experts. After expert computation, results get combined back.

The dispatch/combine is all-to-all communication, but sparse — a token touching experts 5 and 130 only talks to the 2 GPUs hosting those experts, not all GPUs. Far less data moves than TP's all-reduce.

The problem is load imbalance. If certain experts are "hot" (routed to more often), the GPUs hosting them bottleneck while others idle. This is a fundamental MoE challenge, not just an EP problem.

DeepSeek-R1 with 256 experts, EP=8: each GPU holds 32 experts. Scales to massive parameter counts because only ~2 experts per token actually compute. That sparsity is the whole point.

---

## Wide Expert Parallelism (Wide-EP)

Wide-EP is the current state-of-the-art for MoE inference at scale. It combines data-parallel attention with expert-parallel MoE layers and separates prefill from decode into dedicated GPU groups.

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

Three techniques working together:

**Data-parallel attention.** Each GPU runs the full attention computation on its own batch of tokens independently. No communication needed. Pure DP for the attention layers.

**Expert-parallel MoE layers.** Experts distribute across all GPUs in the group. After attention, DeepEP dispatch kernels send tokens to their assigned expert GPUs using sparse all-to-all over RDMA. Prefill uses high-throughput dispatch. Decode uses low-latency dispatch. Different kernels for different phases.

**Prefill/decode disaggregation.** Prefill is compute-heavy. Decode is memory-bandwidth-heavy. They have fundamentally different hardware requirements. Wide-EP separates them onto different GPU groups. NVIDIA's NIXL library transfers KV cache from prefill workers to decode workers over RDMA.

Orchestration requires LeaderWorkerSet (LWS) to manage multi-node pod groups in Kubernetes, and EPLB to periodically rebalance expert placement and replicate hot experts.

The numbers: DeepSeek-R1-0528 on 32 H200 GPUs with llm-d — 16 prefill, 16 decode — achieves ~3,200 input tok/s/GPU and ~3,100 output tok/s/GPU. The communication pattern is sparse all-to-all over InfiniBand RDMA for expert dispatch and point-to-point RDMA for KV cache transfer. Every NIC must reach every other NIC — full-mesh connectivity is non-negotiable.

The minimum viable deployment is 24-32 H200/B200 GPUs with InfiniBand. Not cheap. But the per-GPU efficiency at scale is significantly better than TP-based alternatives for MoE models.

---

## Comparison

| Strategy | What gets split | Communication | Interconnect needed | Latency impact | Throughput | Applies to |
|----------|----------------|---------------|-------------------|----------------|------------|------------|
| **DP** | Nothing (full replica) | None | Any | No change | Linear scaling | Any model |
| **TP** | Layers (horizontal) | All-reduce every layer | NVLink / IB | Reduces TTFT | Moderate | Any model |
| **PP** | Layers (vertical) | Point-to-point per stage | Ethernet OK | Increases | Moderate | Any model |
| **EP** | Experts | Sparse all-to-all | NVLink / IB | Variable | High for MoE | MoE only |
| **Wide-EP** | Experts + PD disagg | Sparse all-to-all + NIXL | IB RDMA (full mesh) | Optimized per phase | Highest | MoE only |

---

## How they combine in practice

Nobody uses one strategy in isolation at scale. Production deployments mix them:

| Combination | What it looks like | Real-world example |
|-------------|-------------------|-------------------|
| **TP + DP** | TP within a node, DP across nodes | 8xH100 node with TP=8, multiple nodes as replicas |
| **TP + PP** | TP within each node, PP across nodes | TP=8 per node, PP=2 across 2 nodes for 400B+ dense models |
| **EP + DP** | DP for attention, EP for experts (Wide-EP) | DeepSeek-R1 on 32 H200s |
| **TP + EP** | TP for attention heads, EP for expert layers | MoE models on a single multi-GPU node |

The emerging direction is N-D parallelism — stacking context parallelism, pipeline parallelism, expert parallelism, and tensor parallelism across different cluster dimensions, with disaggregated prefill and decode tiers running on hardware optimized for each phase. This is where things are heading for frontier model serving.

---

## Decision tree

```
Does the model fit on one GPU?
├── Yes → DP. Add replicas for throughput.
└── No
    ├── Dense model?
    │   ├── GPUs on same node with NVLink? → TP
    │   └── GPUs across nodes on ethernet? → PP (or quantize to fit one GPU)
    └── MoE model?
        ├── Single node? → EP + TP for attention
        └── Multi-node with InfiniBand? → Wide-EP with prefill/decode disaggregation
```

---

## References

- [LLM Inference Parallelism Strategies (Wilson Wu)](https://wilsonwu.me/en/blog/2025/llm-inference-parallelism-in-vllm/) — vLLM performance verification of TP, DP, PP, EP
- [Scaling LLM Inference (Meta Engineering)](https://engineering.fb.com/2025/10/17/ai-research/scaling-llm-inference-innovations-tensor-parallelism-context-parallelism-expert-parallelism/) — Meta's TP, CP, and EP innovations
- [vLLM Large Scale Serving: DeepSeek with Wide-EP](https://vllm.ai/blog/large-scale-serving) — Production benchmarks at 2.2k tok/s per H200
- [Scaling DeepSeek MoEs with vLLM and llm-d (Red Hat)](https://developers.redhat.com/articles/2025/09/08/scaling-deepseek-style-moes-vllm-and-llm-d-using-wide-ep) — llm-d Wide-EP architecture
- [llm-d Wide-EP with LeaderWorkerSet Guide](https://llm-d.ai/docs/guide/Installation/wide-ep-lws) — Kubernetes deployment for Wide-EP
- [Data, Tensor, Pipeline, Expert Parallelism (BentoML)](https://bentoml.com/llm/inference-optimization/data-tensor-pipeline-expert-hybrid-parallelism) — Parallelism overview
- [vLLM Parallelism and Scaling Docs](https://docs.vllm.ai/en/stable/serving/parallelism_scaling/) — Official configuration reference

# llm-d versus Dynamo comparison scenarios

These manifests hold the model, worker image, vLLM configuration, topology, and
GPU allocation constant while changing the orchestration layer.

## Suites

| Suite | Model | Worker image | Purpose |
|---|---|---|---|
| `qwen3-30b-control` | `Qwen/Qwen3-30B-A3B-Instruct-2507-FP8` | `nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.3.1` | Stable no-spec control |
| `nemotron35-dspark` | Nemotron 3.5 Lightning NVFP4 | `nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.0-nemotron-3.5-lightning-dev.1` | Experimental DSpark |

Within a suite, llm-d runs `vllm serve` and Dynamo runs
`python -m dynamo.vllm` from the same image.

Each suite contains aggregate and 1-prefill/1-decode manifests. llm-d is pinned
to `spark-01`; Dynamo is pinned to `spark-02`. Every GPU worker requests one
`nvidia.com/gpu.shared` slice. Do not deploy both modes simultaneously because
each worker loads another model copy into the GB10 unified-memory pool.

## Deploy from controller

Create the common secret once:

```bash
kubectl -n token-labs create secret generic hf-token \
  --from-literal=HF_TOKEN="${HF_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Deploy llm-d:

```bash
cd deploy/models/qwen3-30b-a3b-instruct-2507-fp8/llm-d
MODE=disaggregated NAMESPACE=token-labs helmfile apply
```

Deploy the matching Dynamo scenario from the repository root:

```bash
kubectl apply -f \
  deploy/models/qwen3-30b-a3b-instruct-2507-fp8/dynamo/disaggregated.yaml
```

For Nemotron, use `deploy/models/nemotron-3.5-lightning-30b-a3b-nvfp4/`.
Select `aggregated` or `disaggregated` as needed, and remove the old mode before
deploying another. Both model suites start at 32K context, one sequence, and
0.40 GPU-memory utilization. Nemotron uses one DSpark speculative token on both
P and D workers, as required for compatible NIXL cache metadata.

Repeat benchmarks with the node selectors swapped to control for differences
between `spark-01` and `spark-02`; keep all engine arguments unchanged.

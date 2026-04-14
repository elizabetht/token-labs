# Postmortem: spark-01 Double Node Failure
**Date:** 2026-04-14  
**Severity:** SEV-2 (partial cluster outage, spark-02 unaffected)  
**Duration:** ~90 minutes total (two separate outage windows)  
**Author:** Elizabeth  
**Status:** Resolved

---

## Summary

spark-01 (DGX Spark GB10, 128GB unified memory) went `NotReady` twice on 2026-04-14 during deployment of a 4-model heterogeneous inference stack. The root cause was a large model workload (Qwen3-Coder-Next-FP8, a ~100B+ parameter model with pipeline-parallel execution) whose leader pod entered an `Error` state but did not release GPU memory — holding ~39GB as a zombie process. This reduced available GPU memory below the threshold needed to load Nemotron-Super-120B-A12B-NVFP4, causing an OOM crash loop. The second failure occurred when the qwen3-coder worker successfully joined the Ray cluster after the node rebooted, causing both nodes to attempt loading the full Qwen3-Coder-Next model under resource pressure, likely triggering a thermal or power event on spark-01.

---

## Impact

| Window | Start | End | Duration | Impact |
|--------|-------|-----|----------|--------|
| Outage 1 | ~19:20 UTC | ~21:36 UTC | ~136 min | TRT-LLM and Nemotron offline; spark-02 unaffected |
| Outage 2 | ~22:27 UTC | ~22:31 UTC | ~4 min | Same, shorter recovery |

- **User-facing:** api.tokenlabs.run returned 500 on /v1/chat/completions for models routed to spark-01 backends (Qwen2.5-7B-TRT-LLM). Requests to spark-02 models (DeepSeek-R1, Llama-3.1-8B) continued serving normally.
- **Models affected:** Qwen2.5-7B-Instruct (TRT-LLM), Nemotron-Super-120B (still downloading at time of failure)
- **Models unaffected:** DeepSeek-R1-Distill-Qwen-7B (vLLM), Llama-3.1-8B-Instruct (SGLang)

---

## Timeline

All times UTC on 2026-04-14.

| Time | Event |
|------|-------|
| ~18:40 | Enabled GPU time-slicing (3 replicas/node) on both spark nodes |
| ~18:57 | Deployed 3 new pods: nemotron-120b (spark-01), deepseek-r1 (spark-02), llama-31-8b (spark-02) |
| ~19:00 | Pods pending due to CPU constraints (8 CPU request vs 4 available); reduced to 4 CPU request |
| ~19:04 | All 3 pods scheduled and Running; qwen3-coder-next-vllm-leader in Error on spark-01 |
| ~19:06 | Nemotron crashes: `ValueError: Free memory (62.95 GiB) < desired GPU memory utilization (65.83 GiB)` |
| ~19:10 | Diagnosed: qwen3-coder Error pod holding 39GB GPU as zombie process |
| ~19:12 | Reduced Nemotron gpu-memory-utilization 0.55 → 0.50; redeployed |
| ~19:14 | Nemotron loading cleanly (NemotronHForCausalLM, Marlin NVFP4 backend) |
| ~19:30 | Nemotron enters Terminating; spark-01 node goes NotReady; SSH unreachable |
| ~19:32 | DeepSeek-R1 and Llama-3.1-8B also fail (Longhorn replicas only on spark-01, volumes faulted) |
| ~19:35 | Fixed Longhorn: enabled spark-02 replica scheduling, lowered storage reserved % |
| ~19:45 | Redeployed DeepSeek-R1 and Llama with `HF_HUB_DISABLE_XET=1` (separate xet bug) |
| ~19:55 | Llama-3.1-8B Ready; ~20:05 DeepSeek-R1 Ready. spark-02 fully operational |
| ~21:36 | User power-cycled spark-01. Node returns to Ready |
| ~21:38 | Redeployed TRT-LLM and Nemotron on spark-01 |
| ~21:41 | TRT-LLM Ready and serving Qwen2.5-7B |
| ~22:28 | spark-01 goes NotReady again (2nd failure) |
| ~22:31 | User power-cycled spark-01 again |
| ~22:31 | Removed qwen3-coder manifests from cluster and repo |
| ~22:35 | TRT-LLM Ready; spark-01 stable; all 3 available models serving |

---

## Root Cause Analysis

### Primary: qwen3-coder zombie process holding GPU memory

The `qwen3-coder-next-vllm-leader` pod entered Error state (exit code non-zero from Ray/vLLM failure) but was not immediately killed by the kubelet. The container's Python process remained alive with ~39GB GPU memory allocated. This reduced available GPU on spark-01 from 119.7GB to ~62.95GB free.

Nemotron-Super-120B with `gpu-memory-utilization=0.55` required `0.55 × 119.7 = 65.83 GiB`, exceeding available 62.95 GiB by 2.88 GiB. vLLM's pre-flight check fails hard on this condition, preventing startup.

**Why did the zombie process exist?**  
Kubernetes pod termination on container exit code ≠ 0 with `restartPolicy: Never` marks the pod as Error but does not immediately SIGKILL the underlying container process. The kubelet sends SIGTERM, waits `terminationGracePeriodSeconds` (default 30s), then SIGKILL. However, when the container's primary process is already exited but a child subprocess (Python/CUDA runtime) holds GPU allocations, the OS may not release the GPU context until all processes in the cgroup are killed. This is a known issue with CUDA processes and pod termination.

### Contributing Factor 1: qwen3-coder memory footprint

Qwen3-Coder-Next-FP8 is a pipeline-parallel model (PP=2) targeting both spark nodes. The leader on spark-01 used `gpu-memory-utilization=0.75` (90GB allocation). Even when Error, residual CUDA context held ~39GB. The model was too large for the cluster's memory budget when combined with other workloads.

### Contributing Factor 2: Second failure — likely thermal event

After spark-01 rebooted and qwen3-coder worker joined Ray on spark-02, the leader on spark-01 began loading Qwen3-Coder-Next-FP8. Combined with the new TRT-LLM (Qwen2.5-7B) and Nemotron-120B downloading, spark-01 was under significant compute and memory pressure. The second node failure occurred ~47 minutes after the first reboot, consistent with thermal throttling or a power event under sustained GPU load.

### Contributing Factor 3: Longhorn single-replica configuration

All Longhorn PVC replicas were scheduled only to spark-01 (`allowScheduling: false` on spark-02). When spark-01 went down, newly created PVCs for DeepSeek-R1 and Llama-3.1-8B became Faulted and unattachable, blocking those pods. This amplified the blast radius of the spark-01 failure.

---

## Resolution

1. **Immediate:** Reduced Nemotron `gpu-memory-utilization` from 0.55 → 0.50 to stay within 62.95GB available.
2. **Root fix:** Deleted qwen3-coder-next manifests from the cluster and repository (commit `30e8a3b`). This eliminated the zombie process and freed full GPU on spark-01.
3. **Longhorn fix:** Enabled replica scheduling on spark-02, reduced `storage-minimal-available-percentage` to 10%, and `storageReserved` to 50GB. New volumes now replicate to spark-02, ensuring survivability during spark-01 downtime.
4. **Gateway resilience:** Updated /models catch-all HTTPRoute to fan out across all active backends, preventing 500s when a single backend is down.

---

## Action Items

| # | Action | Owner | Priority | Status |
|---|--------|-------|----------|--------|
| AI-1 | Add Prometheus alert: GPU process memory > 80% of node total for > 5 min | Elizabeth | P1 | Open |
| AI-2 | Set `terminationGracePeriodSeconds: 10` on all inference pods to force-kill GPU processes faster | Elizabeth | P1 | Open |
| AI-3 | Configure node NotReady alert in Grafana (alert within 2 min of node loss) | Elizabeth | P1 | Open |
| AI-4 | Enforce Longhorn replica count ≥ 2 for all model-cache PVCs; add to PVC template | Elizabeth | P2 | Open |
| AI-5 | Define GPU memory budget policy: max total reserved < 85% of node total across all pods | Elizabeth | P2 | Open |
| AI-6 | Add `resources.limits.nvidia.com/gpu-memory` annotation to pods (when Kubernetes supports it) | Elizabeth | P3 | Open |
| AI-7 | Investigate thermal monitoring on GB10 nodes; add temperature metric to Grafana dashboard | Elizabeth | P2 | Open |

---

## Lessons Learned

- **CUDA zombie processes are a real operational hazard.** An Error-state pod is not the same as a freed GPU. Explicit fast termination grace periods and nvidia-smi process monitoring are necessary for multi-tenant GPU clusters.
- **Single-replica Longhorn storage amplifies node failures.** A single-node failure should not cause data unavailability on other nodes. Default to ≥2 replicas on all inference workloads.
- **Memory budget planning must account for worst-case.** Deploying a 90GB-reservation model alongside a 60GB model on a 128GB node leaves no margin for zombie processes or OS overhead. Document memory budgets per node before deploying new workloads.
- **Gateway resilience should be day-one, not day-two.** The /models 500 during spark-01 outage was preventable by fanning out the catch-all route from the start.

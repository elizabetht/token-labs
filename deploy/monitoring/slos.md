# token-labs Service Level Objectives

**Owner:** Elizabeth  
**Last updated:** 2026-04-15  
**Review cadence:** Monthly

---

## Service description

token-labs is a multi-model inference cluster serving four models across four inference runtimes via a single OpenAI-compatible API at `api.tokenlabs.run`. Traffic is routed by model name via Envoy AI Gateway.

---

## SLO definitions

### SLO-1 — Model availability

**Target:** Each model endpoint available ≥ 99.5% of time (30-day rolling window)  
**Error budget:** 0.5% = 3h 39m per 30 days per model  
**Measurement:** `kube_pod_status_ready{namespace="token-labs"}` == 1 for the model's pod  
**Exclusions:** Planned maintenance windows with 24h advance notice

| Model | Pod | Node |
|-------|-----|------|
| nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4 | nemotron-120b-vllm-leader | spark-01 |
| deepseek-ai/DeepSeek-R1-Distill-Qwen-7B | deepseek-r1-7b-vllm-leader | spark-02 |
| meta-llama/Llama-3.1-8B-Instruct | llama-31-8b-sglang-leader | spark-02 |
| Qwen/Qwen2.5-7B-Instruct | qwen25-7b-trtllm-spark02-leader | spark-02 |

---

### SLO-2 — Time to First Token (TTFT) p99

**Measurement window:** 5-minute rolling p99 over completed streaming requests  
**Metric:** `vllm:time_to_first_token_seconds` histogram (vLLM backends); `sglang:time_to_first_token_seconds` (SGLang)

| Model | TTFT p99 target | Rationale |
|-------|----------------|-----------|
| DeepSeek-R1-Distill-Qwen-7B | ≤ 500ms | 7B dense, single GPU |
| Llama-3.1-8B-Instruct | ≤ 500ms | 8B dense, single GPU |
| Qwen2.5-7B-Instruct | ≤ 500ms | 7B dense, single GPU |
| Nemotron-Super-120B-A12B-NVFP4 | ≤ 3000ms | 120B sparse MoE, NVFP4, single GB10 |

---

### SLO-3 — Request error rate

**Target:** HTTP 5xx error rate < 1% over any 5-minute window  
**Metric:** `http_request_duration_seconds_count{status=~"5.."}` / `http_request_duration_seconds_count`

---

### SLO-4 — KV cache saturation

**Target:** GPU KV cache utilization < 90% (sustained >5 min triggers throttling risk)  
**Metric:** `vllm:kv_cache_usage_perc`

---

## Error budget policy

- **> 50% budget consumed in a month:** freeze non-essential changes to that model's deployment
- **> 90% budget consumed:** incident review required before next deployment

---

## Alert routing

| Alert | Severity | Channel |
|-------|----------|---------|
| ModelDown (pod not ready > 2 min) | critical | PagerDuty |
| TTFTSLOBurn (fast burn 1h) | critical | PagerDuty |
| TTFTSLOBurn (slow burn 6h) | warning | Slack |
| KVCacheSaturation | warning | Slack |
| ErrorRateHigh | critical | PagerDuty |

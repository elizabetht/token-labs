# TokenLabs — Monitoring

This directory contains the Grafana dashboard and Prometheus alerting rules for the TokenLabs AI inference fleet.

## Files

| File | Description |
|---|---|
| `ai-factory-dashboard.json` | Grafana dashboard — "AI Factory (Multi-Tenant)" single pane of glass |
| `alerting-rules.yaml` | `PrometheusRule` CRD with P95 TTFT and KV-Cache capacity alerts |

---

## Dashboard Setup

1. In Grafana, go to **Dashboards → Import**.
2. Upload `ai-factory-dashboard.json` or paste its contents.
3. Select your Prometheus datasource when prompted.
4. The dashboard will auto-populate the `$api_key`, `$model_name`, and `$gpu_node` template variables from live label values.

### Template Variables

| Variable | Source metric | Purpose |
|---|---|---|
| `$api_key` | `limitador_calls_per_limit_total` | Filter all tenant-usage panels to one or more API keys |
| `$model_name` | `vllm:gpu_cache_usage_perc` | Filter by served model (e.g., `Llama-3.1-8B-Instruct`) |
| `$gpu_node` | `vllm:gpu_cache_usage_perc` | Filter by GPU worker node (e.g., `spark-01`) |

### Dashboard Sections

| Row | Panels | Key metrics |
|---|---|---|
| 🚦 Top-Line Golden Signals | TTFT, TPOT, Success Rate, Throughput, Queue depth | `vllm:time_to_first_token_seconds`, `vllm:time_per_output_token_seconds`, `vllm:request_success_total` |
| 💰 Tenant Usage (Billable) | Token throughput by API key, Top 10 API keys, Rate-limit events | `limitador_calls_per_limit_total`, `limitador_limited_calls_total` |
| 🖥️ Inference Pool Health | KV-Cache gauge + trend, RDMA/RoCE throughput, GPU framebuffer | `vllm:gpu_cache_usage_perc`, `dcgm_fi_dev_nvlink_bandwidth_total`, `dcgm_fi_dev_fb_used` |
| ⚡ Prefix Cache Efficiency | Hit-rate gauge, hit/miss trend, per-node breakdown, TTFT correlation | `llm_d_prefix_cache_hit_total`, `llm_d_prefix_cache_miss_total` |

---

## Alerting Rules Setup

Apply the `PrometheusRule` to the cluster (requires `prometheus-operator` or `kube-prometheus-stack`):

```bash
kubectl apply -f monitoring/alerting-rules.yaml
```

---

## Runbooks

### `HighTTFTP95` — P95 TTFT exceeds 2 s {#runbook-high-ttft}

**Severity**: warning  
**Condition**: `histogram_quantile(0.95, vllm:time_to_first_token_seconds_bucket)` > 2 s for 5 minutes

**Likely causes**
- KV-Cache near capacity — new requests are queued behind large existing contexts.
- Insufficient InferencePool replicas for current load.
- Large prompt sizes causing slow prefill on a single pod.

**Remediation steps**
1. Check KV-Cache panel in the dashboard — if `KV Cache Usage %` is high (> 85 %), proceed to the `HighGPUCacheUsage` runbook below.
2. Check the **Queued Requests** stat panel. If the queue is growing, the pool is under-provisioned.
3. Scale up InferencePool replicas:
   ```bash
   # Increase decode replicas in the modelservice values and re-deploy
   helm upgrade --reuse-values -f deploy/llm-d/values/modelservice.yaml \
     token-labs-modelservice llm-d/llm-d-modelservice -n token-labs
   ```
4. If caused by one large tenant saturating the pool, use the **Tenant Usage** row to identify the API key and consider reducing their `RateLimitPolicy` request rate.
5. Verify the prefix cache hit rate is healthy (> 40 %). Low hit rates mean every request is doing a full prefill; check EPP routing logs.

---

### `HighGPUCacheUsage` — KV-Cache > 95 % {#runbook-high-cache-usage}

**Severity**: critical  
**Condition**: `vllm:gpu_cache_usage_perc` > 0.95 on any node for 5 minutes

**Likely causes**
- Large batch of long-context requests filling GPU memory.
- `--gpu-memory-utilization` set too high relative to model weight size.
- Insufficient GPU nodes for current traffic level.

**Remediation steps**
1. Identify the affected node using the **KV Cache Usage % per GPU Node** time-series panel.
2. Check `vllm:num_requests_running` and `vllm:num_requests_waiting` to understand current load.
3. **Immediate relief** — reduce `--gpu-memory-utilization` to free headroom (requires pod restart):
   ```yaml
   # In deploy/llm-d/values/modelservice.yaml
   args:
   - "--gpu-memory-utilization=0.35"   # was 0.40
   ```
   Then redeploy:
   ```bash
   helmfile -f deploy/llm-d/helmfile.yaml.gotmpl apply
   ```
4. **Scale out** — add a second decode replica on the same or another GPU node.
5. **Longer term** — reduce maximum context length via `--max-model-len` to cap per-request memory usage.

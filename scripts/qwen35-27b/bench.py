#!/usr/bin/env python3
"""
Qwen3.5-27B ISL/OSL × Concurrency Benchmark
Runs vllm bench serve against a running inference pod and saves results with DCGM metrics.

Usage:
    python3 bench_qwen35_27b.py --framework vllm --model Qwen/Qwen3.5-27B \
        --quantization bf16 --technique baseline \
        --pod qwen35-27b-vllm-bf16-leader --container vllm \
        --output /path/to/results.json

Print technique flags (used by orchestrator):
    python3 bench_qwen35_27b.py --print-technique-flags vllm kv-fp8
"""
import argparse
import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests

# ── Constants ────────────────────────────────────────────────────────────────

SPARK01_VLLM = "/home/nvidia/src/github.com/sara4dev/ai-dynamo-the-hard-way/.venv/bin/vllm"
SPARK02_VLLM = "/home/nvidia/bench-venv/bin/vllm"
SPARK01_HOST = "nvidia@192.168.1.76"
SPARK02_HOST = "nvidia@192.168.1.77"
PROM_URL     = "http://10.111.136.60:9090"
NAMESPACE    = "token-labs"

NODE_CONFIG = {
    "spark-01": {"host": SPARK01_HOST, "vllm": SPARK01_VLLM, "prom_hostname": "spark-01", "hardware": "DGX Spark GB10 spark-01 (SM 12.1, 128GB)", "ld_path": ""},
    "spark-02": {"host": SPARK02_HOST, "vllm": SPARK02_VLLM,  "prom_hostname": "spark-02", "hardware": "DGX Spark GB10 spark-02 (SM 12.1, 128GB)", "ld_path": "", "kubectl_bench": True},
}

COMBOS = [
    (1024, 1024),   # balanced — general chat
    (4096, 1024),   # prefill-heavy — RAG / long-context ingestion
    (1024, 4096),   # decode-heavy — long generation (reports, code)
]
CONCURRENCY_LEVELS = [1, 8, 32]

HARDWARE = "DGX Spark GB10 spark-01 (SM 12.1, 128GB)"  # overridden by --node arg

# Maps technique name → extra flags for each framework.
# These are purely informational from the benchmark script's perspective —
# the orchestrator starts the pod with the right flags before calling this script.
TECHNIQUE_FLAGS = {
    "vllm": {
        "baseline":       [],
        "no-cuda-graph":  ["--enforce-eager"],
        "kv-fp8":         ["--kv-cache-dtype", "fp8"],
        "lmcache-8g":     ["--kv-offloading-backend", "lmcache", "--kv-offloading-size", "8"],
        "lmcache-20g":    ["--kv-offloading-backend", "lmcache", "--kv-offloading-size", "20"],
        "spec-ngram":     ["--speculative-config", '{"method":"ngram","num_speculative_tokens":5,"prompt_lookup_max":4}'],
        "spec-mtp":       ["--speculative-config", '{"method":"deep_seek_mtp","num_speculative_tokens":3}'],
        "kv-fp8+lmcache": ["--kv-cache-dtype", "fp8", "--kv-offloading-backend", "lmcache", "--kv-offloading-size", "8"],
        "kv-fp8+spec":    ["--kv-cache-dtype", "fp8", "--speculative-config", '{"method":"ngram","num_speculative_tokens":5,"prompt_lookup_max":4}'],
        "multi-step":     ["--num-scheduler-steps", "8"],
        "torch-compile":  ["--compilation-config", '{"level":3}'],
    },
    "sglang": {
        "baseline":         [],
        "no-cuda-graph":    ["--disable-cuda-graph"],
        "kv-fp8":           ["--kv-cache-dtype", "fp8"],
        "spec-ngram":       ["--speculative-algorithm", "NGRAM", "--speculative-num-draft-tokens", "5"],
        "spec-mtp":         ["--speculative-algorithm", "NEXTN", "--speculative-num-draft-tokens", "3"],
        "overlap-schedule": ["--enable-overlap-schedule"],
        "kv-fp8+overlap":   ["--kv-cache-dtype", "fp8", "--enable-overlap-schedule"],
        "kv-fp8+spec":      ["--kv-cache-dtype", "fp8", "--speculative-algorithm", "NGRAM", "--speculative-num-draft-tokens", "5"],
    },
    "trtllm": {
        "baseline":       [],
        "kv-fp8":         ["--kv_cache_dtype", "fp8"],
        "no-cuda-graph":  ["--disable_custom_all_reduce"],
    },
}


# ── Prometheus / DCGM ────────────────────────────────────────────────────────

def collect_dcgm(start_ts: float, end_ts: float, prom_hostname: str = "spark-01") -> dict:
    """Query Prometheus for DCGM metrics averaged over [start_ts, end_ts]."""
    metrics = {}
    queries = {
        "gpu_util_avg_pct":  f'avg_over_time(DCGM_FI_DEV_GPU_UTIL{{Hostname="{prom_hostname}"}}[__range__])',
        "power_avg_w":       f'avg_over_time(DCGM_FI_DEV_POWER_USAGE{{Hostname="{prom_hostname}"}}[__range__])',
        "sm_clock_mhz":      f'avg_over_time(DCGM_FI_DEV_SM_CLOCK{{Hostname="{prom_hostname}"}}[__range__])',
        "mem_copy_util_pct": f'avg_over_time(DCGM_FI_DEV_MEM_COPY_UTIL{{Hostname="{prom_hostname}"}}[__range__])',
        "energy_j":          f'increase(DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION{{Hostname="{prom_hostname}"}}[__range__])',
    }
    duration = max(15, int(end_ts - start_ts))
    range_str = f"{duration}s"

    for key, tmpl in queries.items():
        q = tmpl.replace("__range__", range_str)
        try:
            r = requests.get(
                f"{PROM_URL}/api/v1/query",
                params={"query": q, "time": str(end_ts)},
                timeout=10,
            )
            result = r.json().get("data", {}).get("result", [])
            metrics[key] = round(float(result[0]["value"][1]), 2) if result else None
        except Exception:
            metrics[key] = None

    return metrics


# ── Helpers ──────────────────────────────────────────────────────────────────

def run_params(isl, osl, concurrency):
    est_req_s = max(0.1, isl / 4096.0) + osl * 0.25
    n = max(3, min(50, int(280 * concurrency / est_req_s)))
    total_est_s = math.ceil(n / concurrency) * est_req_s
    timeout = max(300, int(total_est_s * 10 + 120))
    return n, timeout


def get_pod_ip(pod):
    r = subprocess.run(
        ["kubectl", "get", "pod", "-n", NAMESPACE, pod, "-o", "jsonpath={.status.podIP}"],
        capture_output=True, text=True, timeout=15,
    )
    return r.stdout.strip()


def parse_output(text):
    def extract(pattern, txt):
        m = re.search(pattern, txt)
        return round(float(m.group(1)), 2) if m else None

    return {
        "throughput_tok_s": extract(r"Output token throughput.*?:\s*([\d.]+)", text),
        "ttft_p50_ms":      extract(r"[Mm]edian TTFT.*?:\s*([\d.]+)", text),
        "ttft_p99_ms":      extract(r"[Pp]99 TTFT.*?:\s*([\d.]+)", text),
        "itl_p50_ms":       extract(r"[Mm]edian ITL.*?:\s*([\d.]+)", text),
        "itl_p99_ms":       extract(r"[Pp]99 ITL.*?:\s*([\d.]+)", text),
    }


# ── Warmup ───────────────────────────────────────────────────────────────────

def _build_bench_cmd(framework, model, bench_url, isl, osl, num_prompts, concurrency, node_cfg, pod, container, dataset="random"):
    """Build the benchmark command list for the given framework."""
    random_flags = f"--random-input-len {isl} --random-output-len {osl} " if dataset == "random" else ""
    dataset_path_flag = "--dataset-path /model-cache/sharegpt.json " if dataset == "sharegpt" else ""

    if framework == "sglang" or node_cfg.get("kubectl_bench"):
        # Run bench inside the pod via kubectl exec — avoids host CUDA version dependency
        if framework == "sglang":
            inner = (
                f"HF_HUB_OFFLINE=0 python3 -m sglang.bench_serving "
                f"--backend sglang-oai "
                f"--base-url http://localhost:8000 "
                f"--model '{model}' "
                f"--dataset-name {dataset} "
                f"{dataset_path_flag}"
                f"{random_flags}"
                f"--num-prompts {num_prompts} "
                f"--max-concurrency {concurrency}"
            )
        else:
            inner = (
                f"HF_HUB_OFFLINE=0 FLASHINFER_DISABLE_VERSION_CHECK=1 vllm bench serve "
                f"--model '{model}' "
                f"--base-url http://localhost:8000 "
                f"--dataset-name {dataset} "
                f"{dataset_path_flag}"
                f"{random_flags}"
                f"--num-prompts {num_prompts} "
                f"--max-concurrency {concurrency}"
            )
        return [
            "kubectl", "exec", "-n", NAMESPACE, pod, "-c", container,
            "--", "bash", "-c", inner,
        ]
    else:
        # vllm / trtllm: SSH to node and use vllm bench serve
        ld_prefix = f"LD_LIBRARY_PATH={node_cfg['ld_path']}:${{LD_LIBRARY_PATH:-}} " if node_cfg.get("ld_path") else ""
        bench_cmd = (
            f"{ld_prefix}HF_HUB_OFFLINE=0 FLASHINFER_DISABLE_VERSION_CHECK=1 {node_cfg['vllm']} bench serve "
            f"--model '{model}' "
            f"--base-url {bench_url} "
            f"--dataset-name {dataset} "
            f"{random_flags}"
            f"--num-prompts {num_prompts} "
            f"--max-concurrency {concurrency}"
        )
        return ["ssh", "-o", "StrictHostKeyChecking=no", node_cfg["host"], bench_cmd]


def warmup(pod, container, num_warmups, model, node_cfg, framework="vllm"):
    """Send warmup requests to heat up the model before measurement."""
    pod_ip = get_pod_ip(pod)
    bench_url = f"http://{pod_ip}:8000" if pod_ip else "http://localhost:8000"
    cmd = _build_bench_cmd(
        framework, model, bench_url, 128, 64, num_warmups, 1, node_cfg, pod, container
    )
    subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    print(f"  Warmup done ({num_warmups} requests)", flush=True)


# ── Benchmark ────────────────────────────────────────────────────────────────

def run_bench(pod, isl, osl, concurrency, framework, model, node_cfg, container="", dataset="random", num_prompts_override=None):
    np, to = run_params(isl, osl, concurrency)
    if num_prompts_override is not None:
        np = num_prompts_override
        to = max(1200, to) if dataset == "sharegpt" else max(600, to)

    pod_ip = get_pod_ip(pod)
    bench_url = f"http://{pod_ip}:8000" if pod_ip else "http://localhost:8000"

    cmd = _build_bench_cmd(
        framework, model, bench_url, isl, osl, np, concurrency, node_cfg, pod, container, dataset=dataset
    )

    print(
        f"  → {framework} ISL{isl}/OSL{osl} c={concurrency} "
        f"(n={np}, timeout={to}s, url={bench_url})...",
        flush=True,
    )
    start_ts = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=to)
    end_ts = time.time()

    if result.returncode != 0:
        print(f"    ERROR: {result.stderr[-600:]}", flush=True)
        return None, start_ts, end_ts

    metrics = parse_output(result.stdout + result.stderr)
    return metrics, start_ts, end_ts


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Qwen3.5-27B benchmark script")

    # Special mode: just print technique flags as JSON and exit
    parser.add_argument("--print-technique-flags", nargs=2, metavar=("FRAMEWORK", "TECHNIQUE"),
                        help="Print technique flags as JSON array and exit")

    # Normal benchmark mode args
    parser.add_argument("--framework",    choices=["vllm", "sglang", "trtllm"])
    parser.add_argument("--model",        help="HuggingFace model ID")
    parser.add_argument("--quantization", choices=["bf16", "fp8", "gptq-int4"])
    parser.add_argument("--technique",    default="baseline",
                        help="Technique label (metadata only — pod already running with flags)")
    parser.add_argument("--pod",          help="Kubernetes pod name")
    parser.add_argument("--container",    help="Container name in the pod")
    parser.add_argument("--output",       help="Path to output JSON file")
    parser.add_argument("--num-warmups",  type=int, default=5,
                        help="Number of warmup requests before measurement (default: 5)")
    parser.add_argument("--node", choices=["spark-01", "spark-02"], default="spark-01",
                        help="Node running the inference pod (controls SSH target and DCGM labels)")
    parser.add_argument("--dataset", default="random", choices=["random", "sharegpt"],
                        help="Benchmark dataset: random (synthetic) or sharegpt (real conversations)")

    args = parser.parse_args()

    # ── --print-technique-flags mode ──
    if args.print_technique_flags:
        fw, tech = args.print_technique_flags
        try:
            flags = TECHNIQUE_FLAGS[fw][tech]
        except KeyError:
            flags = []
        print(json.dumps(flags))
        sys.exit(0)

    # ── Validate benchmark args ──
    required = ["framework", "model", "quantization", "pod", "container", "output"]
    missing = [f for f in required if not getattr(args, f.replace("-", "_"), None)]
    if missing:
        parser.error(f"Missing required arguments: {', '.join('--' + m for m in missing)}")

    node_cfg = NODE_CONFIG[args.node]
    output_path = args.output

    # ── Resume from partial results ──
    try:
        with open(output_path) as f:
            existing = json.load(f)
        results = existing.get("combos", {})
        print(f"Resuming from {output_path}", flush=True)
    except FileNotFoundError:
        results = {}

    active_combos = [(0, 0)] if args.dataset == "sharegpt" else COMBOS
    total = len(active_combos) * len(CONCURRENCY_LEVELS)
    done  = sum(len(v.get("levels", [])) for v in results.values())
    print(f"Starting at {done}/{total} (dataset={args.dataset})", flush=True)

    # ── Warmup ──
    warmup(args.pod, args.container, args.num_warmups, args.model, node_cfg, framework=args.framework)

    # ── Benchmark loop ──
    for isl, osl in active_combos:
        key = "sharegpt" if args.dataset == "sharegpt" else f"ISL{isl}/OSL{osl}"
        existing_levels = results.get(key, {}).get("levels", [])
        completed_c = {lv["concurrency"] for lv in existing_levels}
        remaining = [c for c in CONCURRENCY_LEVELS if c not in completed_c]

        if not remaining:
            print(f"\n=== {key} already complete ===", flush=True)
            continue

        print(f"\n=== {key} (remaining: c={remaining}) ===", flush=True)
        levels = list(existing_levels)

        for c in remaining:
            np_override = max(40, c * 4) if args.dataset == "sharegpt" else None
            metrics, start_ts, end_ts = run_bench(
                args.pod, isl, osl, c, args.framework, args.model, node_cfg,
                container=args.container, dataset=args.dataset, num_prompts_override=np_override,
            )
            if metrics:
                dcgm = collect_dcgm(start_ts, end_ts, node_cfg["prom_hostname"])
                metrics["concurrency"] = c
                metrics["dcgm"] = dcgm
                levels.append(metrics)
                print(
                    f"    tput={metrics.get('throughput_tok_s')} tok/s  "
                    f"TTFT_p50={metrics.get('ttft_p50_ms')}ms  "
                    f"ITL_p50={metrics.get('itl_p50_ms')}ms  "
                    f"gpu={dcgm.get('gpu_util_avg_pct')}%  "
                    f"pwr={dcgm.get('power_avg_w')}W",
                    flush=True,
                )

            done += 1
            results[key] = {"isl": isl, "osl": osl, "dataset": args.dataset, "levels": levels}

            payload = {
                "model":         args.model,
                "framework":     args.framework,
                "quantization":  args.quantization,
                "technique":     args.technique,
                "dataset":       args.dataset,
                "hardware":      node_cfg["hardware"],
                "timestamp":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "progress":      f"{done}/{total}",
                "combos":        results,
            }
            with open(output_path, "w") as f:
                json.dump(payload, f, indent=2)
            print(f"    saved ({done}/{total})", flush=True)

    print(f"\nDone. Results at {output_path}", flush=True)


if __name__ == "__main__":
    main()

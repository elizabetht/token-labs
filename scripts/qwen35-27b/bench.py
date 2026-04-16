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
SPARK01_HOST = "nvidia@192.168.1.76"
PROM_URL     = "http://10.111.136.60:9090"
NAMESPACE    = "token-labs"

COMBOS = [
    (128,  128),
    (512,  256),
    (1024, 512),
    (2048, 512),
]
CONCURRENCY_LEVELS = [1, 4, 8, 16, 32]

HARDWARE = "DGX Spark GB10 spark-01 (SM 12.1, 128GB)"

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

def collect_dcgm(start_ts: float, end_ts: float) -> dict:
    """Query Prometheus for DCGM metrics averaged over [start_ts, end_ts]."""
    metrics = {}
    queries = {
        "gpu_util_avg_pct":  'avg_over_time(DCGM_FI_DEV_GPU_UTIL{Hostname="spark-01"}[__range__])',
        "power_avg_w":       'avg_over_time(DCGM_FI_DEV_POWER_USAGE{Hostname="spark-01"}[__range__])',
        "sm_clock_mhz":      'avg_over_time(DCGM_FI_DEV_SM_CLOCK{Hostname="spark-01"}[__range__])',
        "mem_copy_util_pct": 'avg_over_time(DCGM_FI_DEV_MEM_COPY_UTIL{Hostname="spark-01"}[__range__])',
        "energy_j":          'increase(DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION{Hostname="spark-01"}[__range__])',
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
    est_req_s = max(0.1, isl / 4096.0) + osl * 0.085
    n = max(5, min(50, int(280 * concurrency / est_req_s)))
    total_est_s = math.ceil(n / concurrency) * est_req_s
    timeout = max(300, int(total_est_s * 2 + 120))
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

def warmup(pod, container, num_warmups, model):
    """Send warmup requests to heat up the model before measurement."""
    pod_ip = get_pod_ip(pod)
    bench_url = f"http://{pod_ip}:8000" if pod_ip else "http://localhost:8000"
    ssh_cmd = (
        f"FLASHINFER_DISABLE_VERSION_CHECK=1 {SPARK01_VLLM} bench serve "
        f"--model '{model}' --base-url {bench_url} "
        f"--dataset-name random --random-input-len 128 --random-output-len 64 "
        f"--num-prompts {num_warmups} --max-concurrency 1"
    )
    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", SPARK01_HOST, ssh_cmd],
        capture_output=True, text=True, timeout=300,
    )
    print(f"  Warmup done ({num_warmups} requests)", flush=True)


# ── Benchmark ────────────────────────────────────────────────────────────────

def run_bench(pod, isl, osl, concurrency, framework, model):
    np, to = run_params(isl, osl, concurrency)

    pod_ip = get_pod_ip(pod)
    bench_url = f"http://{pod_ip}:8000" if pod_ip else "http://localhost:8000"

    bench_cmd = (
        f"FLASHINFER_DISABLE_VERSION_CHECK=1 {SPARK01_VLLM} bench serve "
        f"--model '{model}' "
        f"--base-url {bench_url} "
        f"--dataset-name random "
        f"--random-input-len {isl} "
        f"--random-output-len {osl} "
        f"--num-prompts {np} "
        f"--max-concurrency {concurrency}"
    )
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", SPARK01_HOST, bench_cmd]

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
    parser.add_argument("--num-warmups",  type=int, default=10,
                        help="Number of warmup requests before measurement (default: 10)")

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

    output_path = args.output

    # ── Resume from partial results ──
    try:
        with open(output_path) as f:
            existing = json.load(f)
        results = existing.get("combos", {})
        print(f"Resuming from {output_path}", flush=True)
    except FileNotFoundError:
        results = {}

    total = len(COMBOS) * len(CONCURRENCY_LEVELS)
    done  = sum(len(v.get("levels", [])) for v in results.values())
    print(f"Starting at {done}/{total}", flush=True)

    # ── Warmup ──
    warmup(args.pod, args.container, args.num_warmups, args.model)

    # ── Benchmark loop ──
    for isl, osl in COMBOS:
        key = f"ISL{isl}/OSL{osl}"
        existing_levels = results.get(key, {}).get("levels", [])
        completed_c = {lv["concurrency"] for lv in existing_levels}
        remaining = [c for c in CONCURRENCY_LEVELS if c not in completed_c]

        if not remaining:
            print(f"\n=== {key} already complete ===", flush=True)
            continue

        print(f"\n=== {key} (remaining: c={remaining}) ===", flush=True)
        levels = list(existing_levels)

        for c in remaining:
            metrics, start_ts, end_ts = run_bench(
                args.pod, isl, osl, c, args.framework, args.model
            )
            if metrics:
                dcgm = collect_dcgm(start_ts, end_ts)
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
            results[key] = {"isl": isl, "osl": osl, "levels": levels}

            payload = {
                "model":         args.model,
                "framework":     args.framework,
                "quantization":  args.quantization,
                "technique":     args.technique,
                "hardware":      HARDWARE,
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

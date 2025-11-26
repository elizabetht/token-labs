#!/usr/bin/env python
import os
import time
import math
import json
import requests

BASE_URL = os.environ["BENCH_BASE_URL"].rstrip("/")  # e.g. https://<pod>-8000.proxy.runpod.net
MODEL = os.environ.get("BENCH_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
GPU_COST_PER_HOUR = float(os.environ.get("GPU_COST_PER_HOUR", "0.26"))  # dollars/hour

# Retry configuration for transient failures
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

HEADERS = {
    "Content-Type": "application/json",
    # If you front this with LiteLLM or require an API key, add auth here:
    # "Authorization": f"Bearer {os.environ.get('BENCH_API_KEY')}",
}


def make_request_with_retry(url, payload, timeout, request_name="request"):
    """
    Make a POST request with retry logic for transient failures.
    Returns (response_data, elapsed_time) on success.
    """
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        try:
            t0 = time.time()
            resp = requests.post(
                url,
                headers=HEADERS,
                json=payload,
                timeout=timeout,
            )
            t1 = time.time()
            
            # Handle non-200 responses
            if resp.status_code != 200:
                error_text = resp.text[:500] if resp.text else "(empty response)"
                if resp.status_code in (404, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                    print(f"  {request_name}: Got {resp.status_code}, retrying in {RETRY_DELAY}s... (attempt {attempt + 1}/{MAX_RETRIES})")
                    time.sleep(RETRY_DELAY)
                    continue
                raise RuntimeError(f"{request_name} failed: {resp.status_code} {error_text}")
            
            # Try to parse JSON
            try:
                data = resp.json()
            except Exception:
                if attempt < MAX_RETRIES - 1:
                    print(f"  {request_name}: JSON parse failed, retrying in {RETRY_DELAY}s... (attempt {attempt + 1}/{MAX_RETRIES})")
                    time.sleep(RETRY_DELAY)
                    continue
                raise RuntimeError(f"{request_name} failed: status={resp.status_code}, invalid JSON: {resp.text[:500]}")
            
            return data, (t1 - t0)
            
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                print(f"  {request_name}: Network error ({e}), retrying in {RETRY_DELAY}s... (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(RETRY_DELAY)
                continue
            raise RuntimeError(f"{request_name} failed after {MAX_RETRIES} attempts: {e}")
    
    raise RuntimeError(f"{request_name} failed after {MAX_RETRIES} attempts: {last_error}")


def run_prefill_bench(num_requests=20, prompt_repetitions=512):
    """
    Prefill-heavy benchmark: long prompts, tiny completion (1 token).
    Measures input token throughput (T_in).
    """
    total_prompt_tokens = 0
    total_time = 0.0

    text = "hello world " * prompt_repetitions
    messages = [{"role": "user", "content": text}]

    for i in range(num_requests):
        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": 1,
            "temperature": 0.0,
        }
        
        data, elapsed = make_request_with_retry(
            f"{BASE_URL}/v1/chat/completions",
            payload,
            timeout=120,
            request_name=f"Prefill request {i+1}/{num_requests}"
        )

        usage = data.get("usage", {})
        total_prompt_tokens += usage.get("prompt_tokens", 0)
        total_time += elapsed

    if total_time == 0:
        return 0.0, total_prompt_tokens, total_time

    t_in = total_prompt_tokens / total_time
    return t_in, total_prompt_tokens, total_time


def run_decode_bench(num_requests=20, max_tokens=256):
    """
    Decode-heavy benchmark: short prompts, larger completions.
    Measures output token throughput (T_out).
    """
    total_completion_tokens = 0
    total_time = 0.0

    messages = [{"role": "user", "content": "Explain something interesting about large language models."}]

    for i in range(num_requests):
        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        
        data, elapsed = make_request_with_retry(
            f"{BASE_URL}/v1/chat/completions",
            payload,
            timeout=300,
            request_name=f"Decode request {i+1}/{num_requests}"
        )

        usage = data.get("usage", {})
        total_completion_tokens += usage.get("completion_tokens", 0)
        total_time += elapsed

    if total_time == 0:
        return 0.0, total_completion_tokens, total_time

    t_out = total_completion_tokens / total_time
    return t_out, total_completion_tokens, total_time


def cost_per_million_tokens(tokens_per_second, gpu_cost_per_hour):
    if tokens_per_second <= 0:
        return math.inf
    # Cost per 1M tokens = (hourly_cost * 1e6) / (TPS * 3600)
    return gpu_cost_per_hour * 1_000_000 / (tokens_per_second * 3600.0)


def main():
    print(f"Benchmarking model={MODEL} at {BASE_URL}")
    print(f"GPU_COST_PER_HOUR={GPU_COST_PER_HOUR}")

    # Prefill benchmark
    t_in, total_in_tokens, t_in_time = run_prefill_bench()
    cost_in = cost_per_million_tokens(t_in, GPU_COST_PER_HOUR)

    # Decode benchmark
    t_out, total_out_tokens, t_out_time = run_decode_bench()
    cost_out = cost_per_million_tokens(t_out, GPU_COST_PER_HOUR)

    # Metadata from CI env (if available)
    commit_sha = os.environ.get("GITHUB_SHA", "")
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    image_tag = os.environ.get("BENCH_IMAGE_TAG", "")

    result = {
        "model": MODEL,
        "base_url": BASE_URL,
        "gpu_cost_per_hour": GPU_COST_PER_HOUR,
        "commit_sha": commit_sha,
        "ref": ref_name,
        "image_tag": image_tag,
        "prefill": {
            "tokens_per_second": t_in,
            "total_tokens": total_in_tokens,
            "total_time_sec": t_in_time,
            "cost_per_million_tokens": cost_in,
        },
        "decode": {
            "tokens_per_second": t_out,
            "total_tokens": total_out_tokens,
            "total_time_sec": t_out_time,
            "cost_per_million_tokens": cost_out,
        },
    }

    print("\n=== Benchmark summary (JSON) ===")
    print(json.dumps(result, indent=2))

    # Write to JSON file for artifact upload
    out_path = os.environ.get("BENCH_RESULTS_PATH", "bench_results.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    # Optional: write to GitHub Actions summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write("## vLLM Benchmark Results\n\n")
            f.write(f"- Model: `{MODEL}`\n")
            f.write(f"- Base URL: `{BASE_URL}`\n")
            f.write(f"- Image: `{image_tag}`\n")
            f.write(f"- Commit: `{commit_sha}` (`{ref_name}`)\n")
            f.write(f"- GPU cost: `${GPU_COST_PER_HOUR}/hour`\n\n")
            f.write("### Prefill (input tokens)\n")
            f.write(f"- Tokens/sec: **{t_in:.2f}**\n")
            f.write(f"- Cost per 1M input tokens: **${cost_in:.4f}**\n\n")
            f.write("### Decode (output tokens)\n")
            f.write(f"- Tokens/sec: **{t_out:.2f}**\n")
            f.write(f"- Cost per 1M output tokens: **${cost_out:.4f}**\n\n")


if __name__ == "__main__":
    main()

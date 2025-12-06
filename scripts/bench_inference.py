#!/usr/bin/env python
"""
Concurrent vLLM Benchmark Script

Sends N requests concurrently using asyncio/httpx to allow vLLM to batch them.
This provides realistic throughput numbers for batched workloads.
"""
import os
import time
import math
import json
import asyncio
import statistics
from dataclasses import dataclass, field

import httpx

# Configuration from environment
BASE_URL = os.environ["BENCH_BASE_URL"].rstrip("/")
MODEL = os.environ.get("BENCH_MODEL", "meta-llama/Llama-3.1-8B-Instruct")

# DGX Spark economics:
# - Hardware: $4,000 amortized over 3 years 24/7 = $4000 / 26280 hours ≈ $0.15/hour
# - Electricity: 48.5W × $0.135/kWh ≈ $0.0066/hour  
# - Total: ~$0.16/hour (dominated by hardware amortization)
# Cost per 1M tokens = ($0.16/hour ÷ tokens_per_second) × 1,000,000 / 3600
DGX_COST_PER_HOUR = float(os.environ.get("DGX_COST_PER_HOUR", "0.16"))

# Concurrency settings
CONCURRENCY = int(os.environ.get("CONCURRENCY", "32"))  # Parallel requests
NUM_REQUESTS = int(os.environ.get("NUM_REQUESTS", "64"))  # Total requests per benchmark
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "128"))  # Tokens to generate

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]  # Exponential backoff


@dataclass
class RequestResult:
    """Result from a single request."""
    success: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_sec: float = 0.0
    error: str = ""


@dataclass
class BenchmarkResult:
    """Aggregated benchmark results."""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_wall_time_sec: float = 0.0
    successful_requests: int = 0
    failed_requests: int = 0
    latencies: list = field(default_factory=list)
    
    @property
    def prompt_tokens_per_sec(self) -> float:
        if self.total_wall_time_sec <= 0:
            return 0.0
        return self.total_prompt_tokens / self.total_wall_time_sec
    
    @property
    def completion_tokens_per_sec(self) -> float:
        if self.total_wall_time_sec <= 0:
            return 0.0
        return self.total_completion_tokens / self.total_wall_time_sec
    
    @property
    def p50_latency(self) -> float:
        if not self.latencies:
            return 0.0
        return statistics.median(self.latencies)
    
    @property
    def p95_latency(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]
    
    @property
    def p99_latency(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]


async def make_request(
    client: httpx.AsyncClient,
    payload: dict,
    request_id: int,
    timeout: float = 120.0,
) -> RequestResult:
    """Make a single async request with retry logic."""
    url = f"{BASE_URL}/v1/chat/completions"
    
    for attempt in range(MAX_RETRIES):
        try:
            t0 = time.perf_counter()
            response = await client.post(
                url,
                json=payload,
                timeout=timeout,
            )
            latency = time.perf_counter() - t0
            
            if response.status_code != 200:
                if response.status_code in (404, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    print(f"  Request {request_id}: Got {response.status_code}, retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue
                return RequestResult(
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text[:200]}"
                )
            
            data = response.json()
            usage = data.get("usage", {})
            
            return RequestResult(
                success=True,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                latency_sec=latency,
            )
            
        except httpx.TimeoutException:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"  Request {request_id}: Timeout, retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            return RequestResult(success=False, error="Timeout after retries")
            
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"  Request {request_id}: Error ({e}), retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            return RequestResult(success=False, error=str(e))
    
    return RequestResult(success=False, error="Max retries exceeded")


async def run_concurrent_benchmark(
    messages: list,
    max_tokens: int,
    num_requests: int,
    concurrency: int,
    timeout: float = 120.0,
    bench_name: str = "benchmark",
) -> BenchmarkResult:
    """
    Run benchmark with concurrent requests.
    
    Sends requests in batches of `concurrency` size to allow vLLM to batch them.
    """
    print(f"\n{bench_name}: Sending {num_requests} requests with concurrency={concurrency}")
    
    result = BenchmarkResult()
    
    async with httpx.AsyncClient(
        headers={"Content-Type": "application/json"},
        http2=True,  # Enable HTTP/2 for better connection reuse
    ) as client:
        
        # Process requests in batches
        wall_start = time.perf_counter()
        
        for batch_start in range(0, num_requests, concurrency):
            batch_end = min(batch_start + concurrency, num_requests)
            batch_size = batch_end - batch_start
            
            # Create tasks for this batch
            tasks = []
            for i in range(batch_start, batch_end):
                payload = {
                    "model": MODEL,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                }
                tasks.append(make_request(client, payload, i + 1, timeout))
            
            # Execute batch concurrently
            batch_results = await asyncio.gather(*tasks)
            
            # Aggregate results
            for r in batch_results:
                if r.success:
                    result.successful_requests += 1
                    result.total_prompt_tokens += r.prompt_tokens
                    result.total_completion_tokens += r.completion_tokens
                    result.latencies.append(r.latency_sec)
                else:
                    result.failed_requests += 1
                    print(f"  Failed: {r.error}")
            
            # Progress update
            completed = batch_end
            print(f"  Progress: {completed}/{num_requests} requests completed")
        
        result.total_wall_time_sec = time.perf_counter() - wall_start
    
    return result


async def run_prefill_benchmark() -> BenchmarkResult:
    """
    Prefill-heavy benchmark: long prompts, minimal output.
    Measures input token throughput.
    """
    # Create a long prompt (~1000 tokens)
    long_text = "The quick brown fox jumps over the lazy dog. " * 100
    messages = [{"role": "user", "content": long_text}]
    
    return await run_concurrent_benchmark(
        messages=messages,
        max_tokens=1,  # Minimal output to focus on prefill
        num_requests=NUM_REQUESTS,
        concurrency=CONCURRENCY,
        timeout=120.0,
        bench_name="PREFILL benchmark",
    )


async def run_decode_benchmark() -> BenchmarkResult:
    """
    Decode-heavy benchmark: short prompts, larger completions.
    Measures output token throughput.
    """
    messages = [{"role": "user", "content": "Write a detailed essay about the history of artificial intelligence."}]
    
    return await run_concurrent_benchmark(
        messages=messages,
        max_tokens=MAX_NEW_TOKENS,
        num_requests=NUM_REQUESTS,
        concurrency=CONCURRENCY,
        timeout=300.0,
        bench_name="DECODE benchmark",
    )


def cost_per_million_tokens(tokens_per_second: float, cost_per_hour: float) -> float:
    """
    Calculate cost per 1M tokens based on throughput and hourly cost.
    
    Formula: (cost_per_hour / tokens_per_second) × 1,000,000 / 3600
    This converts $/hour to $/token, then scales to per-million.
    """
    if tokens_per_second <= 0:
        return math.inf
    return cost_per_hour * 1_000_000 / (tokens_per_second * 3600.0)


async def main_async():
    print("=" * 60)
    print("vLLM Concurrent Benchmark")
    print("=" * 60)
    print(f"Model: {MODEL}")
    print(f"Base URL: {BASE_URL}")
    print(f"DGX Spark Cost: ${DGX_COST_PER_HOUR}/hour")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Requests per benchmark: {NUM_REQUESTS}")
    print(f"Max new tokens (decode): {MAX_NEW_TOKENS}")
    print("=" * 60)
    
    # Run benchmarks
    prefill_result = await run_prefill_benchmark()
    decode_result = await run_decode_benchmark()
    
    # Calculate costs based on DGX Spark economics
    prefill_tps = prefill_result.prompt_tokens_per_sec
    decode_tps = decode_result.completion_tokens_per_sec
    
    cost_in = cost_per_million_tokens(prefill_tps, DGX_COST_PER_HOUR)
    cost_out = cost_per_million_tokens(decode_tps, DGX_COST_PER_HOUR)
    
    # Print summary
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    
    print("\n### Prefill (Input Tokens)")
    print(f"  Successful requests: {prefill_result.successful_requests}/{NUM_REQUESTS}")
    print(f"  Total input tokens: {prefill_result.total_prompt_tokens:,}")
    print(f"  Wall time: {prefill_result.total_wall_time_sec:.2f}s")
    print(f"  Throughput: {prefill_tps:.2f} tokens/sec")
    print(f"  Cost per 1M tokens: ${cost_in:.4f}")
    print(f"  Latency P50/P95/P99: {prefill_result.p50_latency:.2f}s / {prefill_result.p95_latency:.2f}s / {prefill_result.p99_latency:.2f}s")
    
    print("\n### Decode (Output Tokens)")
    print(f"  Successful requests: {decode_result.successful_requests}/{NUM_REQUESTS}")
    print(f"  Total output tokens: {decode_result.total_completion_tokens:,}")
    print(f"  Wall time: {decode_result.total_wall_time_sec:.2f}s")
    print(f"  Throughput: {decode_tps:.2f} tokens/sec")
    print(f"  Cost per 1M tokens: ${cost_out:.4f}")
    print(f"  Latency P50/P95/P99: {decode_result.p50_latency:.2f}s / {decode_result.p95_latency:.2f}s / {decode_result.p99_latency:.2f}s")
    
    # Build result JSON
    commit_sha = os.environ.get("GITHUB_SHA", "")
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    image_tag = os.environ.get("BENCH_IMAGE_TAG", "")
    
    result = {
        "model": MODEL,
        "base_url": BASE_URL,
        "dgx_cost_per_hour": DGX_COST_PER_HOUR,
        "concurrency": CONCURRENCY,
        "num_requests": NUM_REQUESTS,
        "commit_sha": commit_sha,
        "ref": ref_name,
        "image_tag": image_tag,
        "prefill": {
            "tokens_per_second": prefill_tps,
            "total_tokens": prefill_result.total_prompt_tokens,
            "total_time_sec": prefill_result.total_wall_time_sec,
            "cost_per_million_tokens": cost_in,
            "successful_requests": prefill_result.successful_requests,
            "failed_requests": prefill_result.failed_requests,
            "latency_p50": prefill_result.p50_latency,
            "latency_p95": prefill_result.p95_latency,
            "latency_p99": prefill_result.p99_latency,
        },
        "decode": {
            "tokens_per_second": decode_tps,
            "total_tokens": decode_result.total_completion_tokens,
            "total_time_sec": decode_result.total_wall_time_sec,
            "cost_per_million_tokens": cost_out,
            "successful_requests": decode_result.successful_requests,
            "failed_requests": decode_result.failed_requests,
            "latency_p50": decode_result.p50_latency,
            "latency_p95": decode_result.p95_latency,
            "latency_p99": decode_result.p99_latency,
        },
    }
    
    print("\n=== Benchmark summary (JSON) ===")
    print(json.dumps(result, indent=2))
    
    # Write to file
    out_path = os.environ.get("BENCH_RESULTS_PATH", "bench_results.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResults saved to: {out_path}")
    
    # GitHub Actions summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write("## vLLM Benchmark Results (Concurrent)\n\n")
            f.write(f"- Model: `{MODEL}`\n")
            f.write(f"- Base URL: `{BASE_URL}`\n")
            f.write(f"- Image: `{image_tag}`\n")
            f.write(f"- Commit: `{commit_sha}` (`{ref_name}`)\n")
            f.write(f"- DGX Spark cost: `${DGX_COST_PER_HOUR}/hour`\n")
            f.write(f"- Concurrency: `{CONCURRENCY}` | Requests: `{NUM_REQUESTS}`\n\n")
            f.write("### Prefill (input tokens)\n")
            f.write(f"- Throughput: **{prefill_tps:.2f} tok/s**\n")
            f.write(f"- Cost per 1M tokens: **${cost_in:.4f}**\n")
            f.write(f"- Latency P50/P95/P99: {prefill_result.p50_latency:.2f}s / {prefill_result.p95_latency:.2f}s / {prefill_result.p99_latency:.2f}s\n\n")
            f.write("### Decode (output tokens)\n")
            f.write(f"- Throughput: **{decode_tps:.2f} tok/s**\n")
            f.write(f"- Cost per 1M tokens: **${cost_out:.4f}**\n")
            f.write(f"- Latency P50/P95/P99: {decode_result.p50_latency:.2f}s / {decode_result.p95_latency:.2f}s / {decode_result.p99_latency:.2f}s\n\n")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

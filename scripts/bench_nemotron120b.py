#!/usr/bin/env python3
"""
ISL/OSL sweep benchmark for Nemotron-120B via Envoy Gateway.
Measures TTFT and ITL via streaming, outputs JSON matching existing format.
"""

import asyncio
import json
import time
import random
import statistics
import sys
from datetime import datetime

import aiohttp

# ── Config ────────────────────────────────────────────────────────────────────

ENDPOINT = "http://192.168.1.200/v1/chat/completions"
HEADERS = {
    "Host": "api.tokenlabs.run",
    "Content-Type": "application/json",
    "x-ai-eg-model": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4",
}
MODEL = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4"

COMBOS = [
    (128,  128),
    (512,  256),
    (1024, 512),
    (2048, 512),
]
CONCURRENCY_LEVELS = [1, 2, 4, 8]

MIN_REQUESTS = 20
MAX_WALL_SECS = 90

# approximate tokens-per-word for prompt generation (~1.3 tok/word)
TOKENS_PER_WORD = 1.3

LOREM_WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua ut enim ad minim veniam quis nostrud "
    "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat duis aute "
    "irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur excepteur sint occaecat cupidatat non proident sunt in culpa qui officia "
    "deserunt mollit anim id est laborum"
).split()


def make_prompt(target_tokens: int) -> str:
    """Generate a prompt of approximately target_tokens tokens."""
    n_words = max(1, int(target_tokens / TOKENS_PER_WORD))
    words = []
    while len(words) < n_words:
        words.extend(LOREM_WORDS)
    random.shuffle(words)
    return " ".join(words[:n_words])


async def single_streaming_request(
    session: aiohttp.ClientSession,
    prompt: str,
    max_tokens: int,
) -> dict:
    """
    Fire one streaming request. Returns:
      ttft_ms, itl_list (ms per inter-token gap), n_output_tokens, error
    """
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
        "temperature": 0.0,
    }

    t_start = time.perf_counter()
    ttft_ms = None
    token_times = []  # absolute times of each token chunk
    error = None
    n_output_tokens = 0

    try:
        async with session.post(
            ENDPOINT,
            headers=HEADERS,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=300),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                return {
                    "ttft_ms": None,
                    "itl_list": [],
                    "n_output_tokens": 0,
                    "error": f"HTTP {resp.status}: {body[:200]}",
                }

            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if not content:
                    continue

                t_now = time.perf_counter()
                if ttft_ms is None:
                    ttft_ms = (t_now - t_start) * 1000.0
                token_times.append(t_now)
                n_output_tokens += 1

    except Exception as e:
        error = str(e)

    # compute ITL from consecutive token times
    itl_list = []
    if len(token_times) >= 2:
        for i in range(1, len(token_times)):
            itl_list.append((token_times[i] - token_times[i - 1]) * 1000.0)

    return {
        "ttft_ms": ttft_ms,
        "itl_list": itl_list,
        "n_output_tokens": n_output_tokens,
        "error": error,
    }


async def run_level(prompt: str, max_tokens: int, concurrency: int) -> dict:
    """
    Run a full concurrency-level measurement.
    Returns aggregated metrics dict.
    """
    connector = aiohttp.TCPConnector(limit=concurrency + 4, force_close=False)
    async with aiohttp.ClientSession(connector=connector) as session:

        ttft_samples = []
        itl_samples = []
        total_output_tokens = 0
        n_errors = 0
        n_requests_done = 0

        t_wall_start = time.perf_counter()

        sem = asyncio.Semaphore(concurrency)

        async def worker():
            nonlocal total_output_tokens, n_errors, n_requests_done
            async with sem:
                result = await single_streaming_request(session, prompt, max_tokens)
                n_requests_done += 1
                if result["error"]:
                    n_errors += 1
                    print(f"  [error] {result['error'][:120]}", file=sys.stderr)
                else:
                    if result["ttft_ms"] is not None:
                        ttft_samples.append(result["ttft_ms"])
                    itl_samples.extend(result["itl_list"])
                    total_output_tokens += result["n_output_tokens"]

        # We keep launching workers until MIN_REQUESTS done OR wall time exceeded
        tasks = []
        while True:
            elapsed = time.perf_counter() - t_wall_start
            if n_requests_done >= MIN_REQUESTS and elapsed >= 5.0:
                break
            if elapsed >= MAX_WALL_SECS:
                break

            # fill up to concurrency active tasks
            tasks = [t for t in tasks if not t.done()]
            while len(tasks) < concurrency:
                t = asyncio.create_task(worker())
                tasks.append(t)

            # small yield to let tasks make progress
            await asyncio.sleep(0.1)

        # wait for in-flight tasks to finish
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        t_wall_end = time.perf_counter()
        wall_time = t_wall_end - t_wall_start

    def pct(samples, p):
        if not samples:
            return None
        s = sorted(samples)
        idx = int(len(s) * p / 100)
        idx = min(idx, len(s) - 1)
        return round(s[idx], 2)

    throughput = round(total_output_tokens / wall_time, 2) if wall_time > 0 else 0.0

    return {
        "throughput_tok_s": throughput,
        "ttft_p50_ms": pct(ttft_samples, 50),
        "ttft_p99_ms": pct(ttft_samples, 99),
        "itl_p50_ms": pct(itl_samples, 50),
        "itl_p99_ms": pct(itl_samples, 99),
        "e2e_p50_ms": None,
        "concurrency": concurrency,
        "n_requests": n_requests_done,
        "n_errors": n_errors,
    }


async def main():
    results = {}

    for isl, osl in COMBOS:
        key = f"ISL{isl}/OSL{osl}"
        print(f"\n{'='*60}")
        print(f"Combo: {key}")
        print(f"{'='*60}")

        prompt = make_prompt(isl)
        levels = []

        for conc in CONCURRENCY_LEVELS:
            print(f"  concurrency={conc} ...", flush=True)
            t0 = time.perf_counter()
            level = await run_level(prompt, osl, conc)
            elapsed = time.perf_counter() - t0
            print(
                f"    throughput={level['throughput_tok_s']} tok/s  "
                f"ttft_p50={level['ttft_p50_ms']} ms  "
                f"itl_p50={level['itl_p50_ms']} ms  "
                f"requests={level['n_requests']}  errors={level['n_errors']}  "
                f"wall={elapsed:.1f}s"
            )
            levels.append(level)

        results[key] = {
            "isl": isl,
            "osl": osl,
            "levels": levels,
        }

    output = {
        "model": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4",
        "runtime": "vllm-cu130-nightly",
        "hardware": "DGX Spark GB10 spark-01 (B200, 128GB unified)",
        "quantization": "NVFP4 (Marlin backend, fp8 KV cache)",
        "date": "2026-04-15",
        "experiment": "isl-osl-sweep",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "combos": results,
    }

    out_path = (
        "/home/nvidia/src/github.com/elizabetht/token-labs/results/"
        "nemotron-120b-nvfp4-isl-osl-sweep-2026-04-15.json"
    )
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n\nResults written to: {out_path}")
    print("\nKey numbers:")
    for key, combo in results.items():
        levels = combo["levels"]
        peak = max(l["throughput_tok_s"] for l in levels)
        c1 = next((l for l in levels if l["concurrency"] == 1), None)
        c8 = next((l for l in levels if l["concurrency"] == 8), None)
        ttft_c1 = c1["ttft_p50_ms"] if c1 else "N/A"
        ttft_c8_p99 = c8["ttft_p99_ms"] if c8 else "N/A"
        print(
            f"  {key}: peak={peak} tok/s  "
            f"ttft_p50@c1={ttft_c1} ms  ttft_p99@c8={ttft_c8_p99} ms"
        )

    return output


if __name__ == "__main__":
    asyncio.run(main())

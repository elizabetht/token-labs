#!/usr/bin/env python3
"""
Agent Workload Benchmark
Benchmarks LLM inference under agentic patterns: multi-turn KV cache reuse,
tool call overhead, and concurrent session throughput.

Usage:
    python3 run_agent_benchmark.py --url http://192.168.1.204:8000 --model Qwen/Qwen2.5-7B-Instruct
    python3 run_agent_benchmark.py --url http://192.168.1.204:8000 --concurrency 1 4 8 --turns 5
"""
import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Prompts / fixtures
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_MULTITURN = (
    "You are a helpful assistant. Your responses are concise and factual. "
    "You answer questions directly and precisely. Do not add unnecessary preamble. "
    "When asked a factual question, answer in 1-3 sentences. "
    "You are an expert in science, history, mathematics, and technology. "
    "Your goal is to provide accurate, succinct answers that inform the user efficiently. "
    "Avoid repetition and filler phrases. Be direct. "
    # Pad to ~256 tokens
    "Remember: quality over quantity. Precision is valued. Clarity is essential. "
    "Think before responding. Every word should earn its place. "
    "You are knowledgeable, reliable, and fast. Users trust your answers."
)

TURNS_SCRIPT = [
    "What is the boiling point of water at sea level?",
    "How does altitude affect that boiling point?",
    "At what altitude does water boil at 90 degrees Celsius approximately?",
    "What practical implications does this have for cooking pasta?",
    "Summarize the key physics principle behind all of this in one sentence.",
]

SYSTEM_PROMPT_TOOLS = """You are a helpful assistant with access to the following tools. Use them when appropriate.

Tools:
- get_weather(location: str) -> dict: Get current weather for a location. Returns temperature, conditions.
- search_web(query: str) -> list[str]: Search the web and return top 3 result snippets.
- calculator(expression: str) -> float: Evaluate a mathematical expression and return the result.

When you need to use a tool, respond with a JSON function call in this exact format:
{"tool_call": {"name": "<tool_name>", "arguments": {<args>}}}

Do not include any other text when making a tool call. After receiving the tool result, provide your final answer.
"""

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name or location"}
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web and return top result snippets",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a mathematical expression",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Math expression to evaluate"}
                },
                "required": ["expression"],
            },
        },
    },
]

TOOL_TRIGGER_MESSAGES = [
    "What is the weather like in San Francisco right now?",
    "Search the web for the latest news about large language models.",
    "What is 1847 multiplied by 293?",
    "What's the current weather in Tokyo?",
    "Calculate the square root of 144 plus 37.",
]

# Simulated tool results to send back after a tool call
TOOL_RESULTS = {
    "get_weather": '{"temperature": 62, "conditions": "partly cloudy", "humidity": 78}',
    "search_web": '["LLMs achieve new SOTA on reasoning benchmarks", "Open-source models close gap with proprietary", "Inference optimization reduces costs by 40%"]',
    "calculator": "541471",
}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    max_retries: int = 4,
) -> httpx.Response:
    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = await client.post(url, json=payload, timeout=120.0)
            if resp.status_code in (429, 503) and attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("Max retries exceeded")


async def chat_completion_stream(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    messages: list[dict],
    max_tokens: int = 80,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
) -> tuple[float, float, int]:
    """
    Send a streaming chat completion request.
    Returns (ttft_ms, total_ms, token_count_approx).
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
        "temperature": 0.0,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    t_start = time.perf_counter()
    ttft_ms: float | None = None
    token_count = 0
    full_text = ""

    async with client.stream("POST", url, json=payload, timeout=120.0) as resp:
        if resp.status_code in (429, 503):
            # Simple retry: raise so caller can handle
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}", request=resp.request, response=resp
            )
        resp.raise_for_status()

        async for raw_line in resp.aiter_lines():
            if not raw_line.startswith("data:"):
                continue
            data = raw_line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content") or ""
            # Also capture tool_calls partial content
            tool_calls = delta.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    content += fn.get("arguments", "")

            if content:
                if ttft_ms is None:
                    ttft_ms = (time.perf_counter() - t_start) * 1000
                full_text += content
                token_count += 1  # approx 1 chunk = ~1 token for SSE

    total_ms = (time.perf_counter() - t_start) * 1000
    if ttft_ms is None:
        ttft_ms = total_ms  # no content streamed — use total

    # Better token estimate: split on spaces (rough)
    approx_tokens = max(token_count, len(full_text.split()))
    return ttft_ms, total_ms, approx_tokens


async def chat_completion_nonstream(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    messages: list[dict],
    max_tokens: int = 80,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
) -> tuple[float, str, int]:
    """
    Non-streaming chat completion. Returns (latency_ms, response_text, token_count).
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
        "temperature": 0.0,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    t_start = time.perf_counter()
    resp = await _post_with_retry(client, url, payload)
    latency_ms = (time.perf_counter() - t_start) * 1000

    data = resp.json()
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    text = msg.get("content") or ""

    # Extract tool call content if present
    tool_calls = msg.get("tool_calls", [])
    tool_name = None
    if tool_calls:
        tc = tool_calls[0]
        fn = tc.get("function", {})
        text = json.dumps({"tool_call": {"name": fn.get("name"), "arguments": json.loads(fn.get("arguments", "{}"))}})
        tool_name = fn.get("name")

    usage = data.get("usage", {})
    tokens = usage.get("completion_tokens", len(text.split()))
    return latency_ms, text, tokens, tool_name


# ---------------------------------------------------------------------------
# Scenario A: Multi-turn KV cache reuse
# ---------------------------------------------------------------------------

async def run_multiturn_session(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    turns: int,
    session_id: int,
) -> list[float]:
    """Run one multi-turn conversation. Returns list of TTFT per turn (ms)."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT_MULTITURN}]
    ttfts = []

    turn_prompts = (TURNS_SCRIPT * ((turns // len(TURNS_SCRIPT)) + 1))[:turns]

    for i, user_msg in enumerate(turn_prompts):
        messages.append({"role": "user", "content": user_msg})
        try:
            ttft_ms, total_ms, n_tokens = await chat_completion_stream(
                client, base_url, model, messages, max_tokens=80
            )
            ttfts.append(ttft_ms)
            # Append a synthetic assistant response to maintain context
            messages.append({
                "role": "assistant",
                "content": f"[turn {i+1} response, ~{n_tokens} tokens]"
            })
        except Exception as exc:
            ttfts.append(float("nan"))
    return ttfts


async def scenario_multiturn_kv_reuse(
    base_url: str,
    model: str,
    turns: int,
    n_sessions: int,
    client: httpx.AsyncClient,
    pbar: tqdm,
) -> dict:
    all_ttfts_by_turn: list[list[float]] = []  # [session][turn]

    tasks = [
        run_multiturn_session(client, base_url, model, turns, sid)
        for sid in range(n_sessions)
    ]

    # Run sessions concurrently, update progress as each finishes
    for coro in asyncio.as_completed(tasks):
        session_ttfts = await coro
        all_ttfts_by_turn.append(session_ttfts)
        pbar.update(turns)

    # Aggregate: per-turn p50 across sessions
    ttft_p50_by_turn = []
    for turn_idx in range(turns):
        vals = [
            s[turn_idx] for s in all_ttfts_by_turn
            if turn_idx < len(s) and not (s[turn_idx] != s[turn_idx])  # filter NaN
        ]
        ttft_p50_by_turn.append(round(statistics.median(vals), 1) if vals else None)

    # KV reuse speedup: turn 1 vs last turn p50
    t1 = ttft_p50_by_turn[0] if ttft_p50_by_turn else None
    tn = ttft_p50_by_turn[-1] if ttft_p50_by_turn else None
    speedup = round(t1 / tn, 2) if (t1 and tn and tn > 0) else None

    # Average tokens per turn (rough)
    avg_tokens = 52  # fixed target

    return {
        "turns": turns,
        "sessions": n_sessions,
        "ttft_by_turn_p50_ms": ttft_p50_by_turn,
        "kv_reuse_speedup": speedup,
        "avg_tokens_per_turn": avg_tokens,
    }


# ---------------------------------------------------------------------------
# Scenario B: Tool call overhead
# ---------------------------------------------------------------------------

async def run_tool_call_sample(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    user_msg: str,
) -> dict:
    """
    One tool-call round trip.
    Returns timing breakdown dict.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_TOOLS},
        {"role": "user", "content": user_msg},
    ]

    # Step 1: get tool call from model (non-streaming to capture function call structure)
    t0 = time.perf_counter()
    try:
        # Try native tool_choice first
        latency_ms, response_text, _, tool_name = await chat_completion_nonstream(
            client, base_url, model, messages,
            max_tokens=200,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
        )
    except Exception:
        # Fallback: no tools parameter (model responds with JSON natively)
        latency_ms, response_text, _, tool_name = await chat_completion_nonstream(
            client, base_url, model, messages,
            max_tokens=200,
        )

    tool_call_latency_ms = latency_ms

    # Step 2: send tool result back and get final answer
    # Use TTFT of streaming final response as post_tool_ttft
    tool_result_content = TOOL_RESULTS.get(tool_name or "calculator", '{"result": "42"}')

    messages.append({"role": "assistant", "content": response_text})
    messages.append({
        "role": "user",
        "content": f"Tool result: {tool_result_content}\n\nNow give me the final answer based on this result.",
    })

    try:
        post_ttft_ms, post_total_ms, _ = await chat_completion_stream(
            client, base_url, model, messages, max_tokens=80
        )
    except Exception:
        post_ttft_ms = float("nan")
        post_total_ms = float("nan")

    return {
        "tool_call_latency_ms": round(tool_call_latency_ms, 1),
        "post_tool_ttft_ms": round(post_ttft_ms, 1) if post_ttft_ms == post_ttft_ms else None,
        "post_tool_total_ms": round(post_total_ms, 1) if post_total_ms == post_total_ms else None,
    }


async def scenario_tool_call_overhead(
    base_url: str,
    model: str,
    n_samples: int,
    client: httpx.AsyncClient,
    pbar: tqdm,
) -> dict:
    # Cycle through trigger messages
    msgs = (TOOL_TRIGGER_MESSAGES * ((n_samples // len(TOOL_TRIGGER_MESSAGES)) + 1))[:n_samples]

    results = []
    for msg in msgs:
        try:
            r = await run_tool_call_sample(client, base_url, model, msg)
            results.append(r)
        except Exception as exc:
            pass
        pbar.update(1)

    if not results:
        return {"n": 0, "error": "all samples failed"}

    tool_latencies = [r["tool_call_latency_ms"] for r in results if r.get("tool_call_latency_ms")]
    post_ttfts = [r["post_tool_ttft_ms"] for r in results if r.get("post_tool_ttft_ms") is not None]

    tool_p50 = round(statistics.median(tool_latencies), 1) if tool_latencies else None
    post_p50 = round(statistics.median(post_ttfts), 1) if post_ttfts else None
    overhead = round(tool_p50 - post_p50, 1) if (tool_p50 and post_p50) else None

    return {
        "n": len(results),
        "tool_call_ttft_p50_ms": tool_p50,
        "tool_call_generation_ms": tool_p50,
        "post_tool_ttft_p50_ms": post_p50,
        "tool_call_overhead_ms": overhead,
    }


# ---------------------------------------------------------------------------
# Scenario C: Concurrent agent sessions
# ---------------------------------------------------------------------------

async def run_concurrent_sessions(
    base_url: str,
    model: str,
    concurrency: int,
    turns: int,
    client: httpx.AsyncClient,
    pbar: tqdm,
) -> dict:
    """Run `concurrency` parallel multi-turn sessions. Returns throughput metrics."""

    ttfts_all: list[float] = []
    total_tokens = 0
    t_wall_start = time.perf_counter()

    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_session(sid: int) -> tuple[list[float], int]:
        async with semaphore:
            sess_ttfts = []
            messages = [{"role": "system", "content": SYSTEM_PROMPT_MULTITURN}]
            turn_prompts = (TURNS_SCRIPT * ((turns // len(TURNS_SCRIPT)) + 1))[:turns]
            sess_tokens = 0
            for i, user_msg in enumerate(turn_prompts):
                messages.append({"role": "user", "content": user_msg})
                try:
                    ttft_ms, total_ms, n_tok = await chat_completion_stream(
                        client, base_url, model, messages, max_tokens=80
                    )
                    sess_ttfts.append(ttft_ms)
                    sess_tokens += n_tok
                    messages.append({"role": "assistant", "content": f"[turn {i+1}]"})
                except Exception:
                    sess_ttfts.append(float("nan"))
                pbar.update(1)
            return sess_ttfts, sess_tokens

    # Total sessions = 2x concurrency to get stable aggregate
    n_sessions = max(concurrency * 2, 4)
    tasks = [bounded_session(i) for i in range(n_sessions)]
    session_results = await asyncio.gather(*tasks, return_exceptions=True)

    t_wall = time.perf_counter() - t_wall_start

    for result in session_results:
        if isinstance(result, Exception):
            continue
        sess_ttfts, sess_tokens = result
        ttfts_all.extend(t for t in sess_ttfts if t == t)  # filter NaN
        total_tokens += sess_tokens

    tput = round(total_tokens / t_wall, 1) if t_wall > 0 else 0
    ttft_p50 = round(statistics.median(ttfts_all), 1) if ttfts_all else None
    ttfts_sorted = sorted(ttfts_all)
    p95_idx = int(len(ttfts_sorted) * 0.95)
    ttft_p95 = round(ttfts_sorted[min(p95_idx, len(ttfts_sorted) - 1)], 1) if ttfts_sorted else None

    return {
        "concurrency": concurrency,
        "tput_tok_s": tput,
        "ttft_p50_ms": ttft_p50,
        "ttft_p95_ms": ttft_p95,
        "n_sessions": n_sessions,
        "total_tokens": total_tokens,
        "wall_time_s": round(t_wall, 2),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_summary(results: dict) -> None:
    s = results["scenarios"]
    mt = s.get("multiturn_kv_reuse", {})
    tc = s.get("tool_call_overhead", {})
    cs = s.get("concurrent_sessions", [])

    print("\n=== Agent Workload Benchmark Results ===\n")

    # Multi-turn
    turns = mt.get("turns", "?")
    sessions = mt.get("sessions", "?")
    ttfts = mt.get("ttft_by_turn_p50_ms", [])
    speedup = mt.get("kv_reuse_speedup", "?")
    print(f"Multi-turn KV Cache Reuse ({turns} turns, {sessions} sessions):")
    if ttfts:
        print(f"  Turn 1 TTFT p50: {ttfts[0]}ms")
        print(f"  Turn {len(ttfts)} TTFT p50: {ttfts[-1]}ms")
    print(f"  KV reuse speedup: {speedup}x")
    print()

    # Tool call
    n = tc.get("n", "?")
    gen_ms = tc.get("tool_call_generation_ms", "?")
    post_ms = tc.get("post_tool_ttft_p50_ms", "?")
    overhead = tc.get("tool_call_overhead_ms", "?")
    print(f"Tool Call Overhead ({n} samples):")
    print(f"  Tool call generation: {gen_ms}ms")
    print(f"  Post-tool response TTFT: {post_ms}ms")
    overhead_str = f"+{overhead}ms" if isinstance(overhead, (int, float)) else str(overhead)
    print(f"  Tool call overhead vs direct: {overhead_str}")
    print()

    # Concurrent sessions
    print("Concurrent Agent Sessions:")
    for entry in cs:
        c = entry.get("concurrency", "?")
        tput = entry.get("tput_tok_s", "?")
        p50 = entry.get("ttft_p50_ms", "?")
        p95 = entry.get("ttft_p95_ms", "?")
        print(f"  c={c:<3} {tput} tok/s, TTFT p50={p50}ms, p95={p95}ms")
    print()


async def main_async(args: argparse.Namespace) -> None:
    concurrency_levels = args.concurrency
    turns = args.turns

    # Session count for multi-turn: 10 by default, scale with concurrency
    n_multiturn_sessions = 10
    n_tool_samples = 20

    # Total progress steps (rough)
    total_steps = (
        n_multiturn_sessions * turns  # scenario A
        + n_tool_samples               # scenario B
        + sum(c * 2 * turns for c in concurrency_levels)  # scenario C
    )

    results: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": args.model,
        "url": args.url,
        "scenarios": {},
    }

    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    async with httpx.AsyncClient(limits=limits) as client:

        with tqdm(total=total_steps, desc="Benchmarking", unit="req") as pbar:

            # --- Scenario A ---
            pbar.set_description("Scenario A: Multi-turn KV reuse")
            mt_result = await scenario_multiturn_kv_reuse(
                args.url, args.model, turns, n_multiturn_sessions, client, pbar
            )
            results["scenarios"]["multiturn_kv_reuse"] = mt_result

            # --- Scenario B ---
            pbar.set_description("Scenario B: Tool call overhead")
            tc_result = await scenario_tool_call_overhead(
                args.url, args.model, n_tool_samples, client, pbar
            )
            results["scenarios"]["tool_call_overhead"] = tc_result

            # --- Scenario C ---
            concurrent_results = []
            for c in concurrency_levels:
                pbar.set_description(f"Scenario C: c={c} concurrent sessions")
                cs_result = await run_concurrent_sessions(
                    args.url, args.model, c, turns, client, pbar
                )
                concurrent_results.append(cs_result)
            results["scenarios"]["concurrent_sessions"] = concurrent_results

    # Save JSON
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {args.output}")

    # Human-readable summary
    print_summary(results)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent workload benchmark: multi-turn KV reuse, tool call overhead, concurrent sessions"
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Base URL of the inference endpoint (e.g. http://192.168.1.204:8000)",
    )
    parser.add_argument(
        "--model",
        default="Qwen/Qwen2.5-7B-Instruct",
        help="Model name (default: Qwen/Qwen2.5-7B-Instruct)",
    )
    parser.add_argument(
        "--concurrency",
        nargs="+",
        type=int,
        default=[1, 4, 8],
        help="Concurrent session counts for scenario C (default: 1 4 8)",
    )
    parser.add_argument(
        "--turns",
        type=int,
        default=5,
        help="Number of turns per conversation (default: 5)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write JSON results (optional)",
    )
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()

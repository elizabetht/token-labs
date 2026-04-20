"""
nano_trtllm.py — TensorRT-LLM core ideas in ~200 lines
=======================================================
TRT-LLM is a production inference library. Under the hood it does three things:

  1. ENGINE BUILD   — convert PyTorch weights to a fused TensorRT engine
                      (operator fusion, kernel selection, quantization)

  2. KV CACHE       — preallocate memory for attention keys/values so each
                      decode step appends O(1) tokens instead of recomputing
                      the entire sequence every step

  3. BATCH MANAGER  — pack many in-flight requests into one GPU call;
                      decode is memory-bandwidth-bound so batching is nearly free

This file uses GPT-2 (124M params) to demonstrate all three with real numbers.

Run:
    pip install transformers torch
    python nano_trtllm.py
"""

import time
import torch
from transformers import GPT2LMHeadModel, GPT2Tokenizer

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


# ─── PHASE 1: ENGINE BUILD ───────────────────────────────────────────────────
# TRT-LLM "builds" a model by:
#   - Tracing the compute graph
#   - Fusing operations (LayerNorm + Linear → single CUDA kernel)
#   - Selecting the fastest kernel implementation for the target GPU
#   - Optionally quantizing weights (INT8 / FP8 / INT4)
#
# The closest PyTorch equivalent is torch.compile(), which does the same
# graph tracing + fusion via the Triton/Inductor backend.
#
# After a build, the engine is fixed to specific batch/sequence sizes.
# TRT-LLM exports the engine as a binary; we keep the model in memory.

def build_engine(model_name: str = "gpt2", use_compile: bool = False):
    print(f"[build] Loading {model_name} ...")
    model = GPT2LMHeadModel.from_pretrained(model_name).to(DEVICE).eval()

    if use_compile:
        print("[build] Compiling with torch.compile (Inductor backend) ...")
        # This traces the graph and emits Triton kernels — same idea as TRT engine
        model = torch.compile(model)

    n_params = sum(p.numel() for p in model.parameters()) / 1e6
    print(f"[build] Engine ready — {n_params:.0f}M params on {DEVICE}")
    return model


# ─── PHASE 2: KV CACHE ───────────────────────────────────────────────────────
# Transformer attention for token t:
#
#   output_t = softmax( Q_t @ K_{0..t}^T / sqrt(d) ) @ V_{0..t}
#
# Without a cache: K and V for tokens 0..t-1 are RECOMPUTED every step.
#   → generating 100 tokens costs 100 full forward passes over growing seqs.
#   → O(n²) total compute.
#
# With a KV cache: store each layer's K and V as tokens are produced.
#   → each decode step only computes K, V for the ONE new token, then appends.
#   → O(n) total compute.
#
# TRT-LLM preallocates a fixed buffer [batch, heads, max_seq_len, head_dim]
# and updates it in-place with pointer arithmetic (no memory copies).
# "Paged KV cache" (vLLM) extends this to handle variable-length sequences
# like OS virtual memory pages.
#
# HuggingFace exposes this as `past_key_values` — a tuple of (K, V) per layer.

def prefill(model, input_ids: torch.Tensor):
    """
    PREFILL PHASE: run the full prompt through the model in one forward pass.

    This fills the KV cache for all prompt positions simultaneously.
    It's compute-bound (many tokens processed in parallel on GPU).

    Returns:
        logits     — shape [batch, vocab_size] for the last token position
        kv_cache   — tuple of (K, V) tensors, one pair per transformer layer
    """
    with torch.no_grad():
        out = model(input_ids, use_cache=True)

    # past_key_values shape per layer: [batch, n_heads, seq_len, head_dim]
    # GPT-2 has 12 layers, 12 heads, head_dim=64 → 12 × 2 tensors cached
    return out.logits[:, -1, :], out.past_key_values


def decode_step(model, last_token_ids: torch.Tensor, past_key_values):
    """
    DECODE PHASE: run the model on ONE new token per sequence.

    past_key_values carries the full history — only the new token's K/V
    is computed and appended. The GPU reads model weights once per step.

    This step is memory-bandwidth-bound, not compute-bound.
    That's why quantization (INT4 halves weight size → halves BW → 2× faster)
    and tensor parallelism (split weights across GPUs) matter most here.
    """
    with torch.no_grad():
        out = model(
            last_token_ids.unsqueeze(-1),   # [batch, 1] — one token per sequence
            past_key_values=past_key_values,
            use_cache=True,
        )
    return out.logits[:, -1, :], out.past_key_values


def greedy_sample(logits: torch.Tensor) -> torch.Tensor:
    """Greedy decoding: pick the highest-probability token."""
    return logits.argmax(dim=-1)  # [batch]


# ─── PHASE 3: GENERATION LOOP ────────────────────────────────────────────────
# TRT-LLM's batch manager runs a loop:
#
#   while requests_pending:
#       prefill new arrivals (chunked to limit latency impact)
#       run one decode step for all active sequences
#       retire finished sequences, admit new ones (in-flight batching)
#
# "In-flight batching" means you don't wait for all sequences to finish
# before starting new ones — the batch is fluid. This maximizes GPU utilization.
#
# Our version: simple synchronous batched prefill → decode.

def generate(
    model,
    tokenizer,
    prompts: list[str],
    max_new_tokens: int = 30,
) -> dict:
    """
    Batched generation with explicit prefill + decode phases.

    In a real system sequences have different lengths and finish at different
    times. TRT-LLM handles this with paged KV cache + dynamic batching.
    We pad all prompts to the same length for simplicity.
    """
    enc = tokenizer(prompts, return_tensors="pt", padding=True).to(DEVICE)
    input_ids = enc["input_ids"]

    # ── PREFILL ───────────────────────────────────────────────────────────────
    t_prefill_start = time.perf_counter()
    logits, kv_cache = prefill(model, input_ids)
    prefill_ms = (time.perf_counter() - t_prefill_start) * 1000

    # ── DECODE ────────────────────────────────────────────────────────────────
    # One step per new token. The kv_cache grows by 1 position each step.
    generated = [[] for _ in prompts]
    last_ids = greedy_sample(logits)   # first new token for each sequence

    t_decode_start = time.perf_counter()
    for _ in range(max_new_tokens):
        for i, tid in enumerate(last_ids.tolist()):
            if tid == tokenizer.eos_token_id:
                continue
            generated[i].append(tid)
        logits, kv_cache = decode_step(model, last_ids, kv_cache)
        last_ids = greedy_sample(logits)
    decode_ms = (time.perf_counter() - t_decode_start) * 1000

    total_new_tokens = sum(len(g) for g in generated)
    tps = total_new_tokens / (decode_ms / 1000) if decode_ms > 0 else 0

    return {
        "texts": [tokenizer.decode(g, skip_special_tokens=True) for g in generated],
        "prefill_ms": round(prefill_ms, 1),
        "decode_ms": round(decode_ms, 1),
        "tokens_per_sec": round(tps, 1),
        "total_new_tokens": total_new_tokens,
    }


# ─── WITHOUT KV CACHE (baseline for comparison) ───────────────────────────────
# TODO(human): implement naive_generate() here.
#
# Your task: generate tokens WITHOUT using past_key_values — i.e., re-run the
# full forward pass on the growing sequence each decode step.
#
# Signature:
#   def naive_generate(model, tokenizer, prompt: str, max_new_tokens: int = 20) -> dict:
#
# Approach:
#   1. Tokenize `prompt` → input_ids  shape [1, seq_len]
#   2. Loop max_new_tokens times:
#        a. Run model(input_ids, use_cache=False)   ← no cache!
#        b. Sample the last logit (greedy)
#        c. Append the new token id to input_ids
#   3. Decode and return timing + text (same shape as generate() above)
#
# Compare its tok/s against generate() to see the KV cache speedup.

def naive_generate(model, tokenizer, prompt: str, max_new_tokens: int = 20) -> dict:
    raise NotImplementedError(
        "Implement me! See the TODO above for the full spec."
    )


# ─── DEMO ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tokenizer = GPT2Tokenizer.from_pretrained("gpt2")
    tokenizer.pad_token = tokenizer.eos_token

    model = build_engine("gpt2", use_compile=False)

    prompts = [
        "The key insight of transformer attention is",
        "TensorRT-LLM speeds up inference by",
        "The KV cache stores keys and values so that",
    ]

    print(f"\nBatch size: {len(prompts)}  |  max_new_tokens: 30  |  device: {DEVICE}")
    print("─" * 60)

    result = generate(model, tokenizer, prompts, max_new_tokens=30)

    print(f"Prefill : {result['prefill_ms']} ms")
    print(f"Decode  : {result['decode_ms']} ms  →  {result['tokens_per_sec']} tok/s")
    print(f"Tokens  : {result['total_new_tokens']} total new tokens across batch")
    print("─" * 60)

    for prompt, text in zip(prompts, result["texts"]):
        print(f"\nPrompt : {prompt}")
        print(f"Output : {text}")

    print("\n─" * 60)
    print("Next step: implement naive_generate() and compare tok/s to see the")
    print("KV cache speedup. On CPU with GPT-2 expect ~3-5× difference.")
    print("On GPU with a large model (7B+) the gap widens to 10-20×.")

"""
RL with vLLM — Hands-on Demo
==============================
What this teaches:
  1. The GRPO training loop used in DeepSeek-R1, Qwen-3, etc.
  2. Where vLLM fits: it IS the rollout engine
  3. Reward design — the most important piece (OpenPipe angle)

Run against any vLLM OpenAI-compatible server:
  python3 rl_demo.py --url http://10.244.2.41:8000 --model Qwen/Qwen2.5-7B-Instruct
"""

import argparse
import re
import statistics
import textwrap
from openai import OpenAI

# ── config ─────────────────────────────────────────────────────────────────────
TASKS = [
    {"prompt": "What is 47 + 38?",          "answer": 85},
    {"prompt": "What is 123 - 67?",          "answer": 56},
    {"prompt": "What is 9 × 8?",            "answer": 72},
    {"prompt": "What is 144 ÷ 12?",         "answer": 12},
    {"prompt": "What is 25 + 75?",          "answer": 100},
    {"prompt": "What is 200 - 143?",        "answer": 57},
    {"prompt": "What is 7 × 13?",           "answer": 91},
    {"prompt": "What is 256 ÷ 16?",         "answer": 16},
]

SYSTEM_PROMPT = (
    "You are a math assistant. Answer ONLY with the final number, "
    "no explanation, no units. Example: 42"
)

N_COMPLETIONS = 4   # G in GRPO = Group size (completions per prompt)
TEMPERATURE   = 0.8  # >0 so outputs vary — essential for exploration


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 1: THE RL TRAINING LOOP
# ──────────────────────────────────────────────────────────────────────────────
def print_loop_overview():
    print("""
╔══════════════════════════════════════════════════════════════════════╗
║              THE RL TRAINING LOOP (GRPO / REINFORCE)                ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║   ┌─────────────┐     ┌──────────────┐     ┌────────────────────┐  ║
║   │  Prompts    │────▶│  vLLM        │────▶│  Completions       │  ║
║   │  (tasks)    │     │  (rollouts)  │     │  G per prompt      │  ║
║   └─────────────┘     └──────────────┘     └────────┬───────────┘  ║
║                                                      │              ║
║   ┌─────────────────────────────────────────────────▼───────────┐  ║
║   │  Reward Function  →  score each completion  →  [r1,r2,r3,r4]│  ║
║   └─────────────────────────────────────────────────┬───────────┘  ║
║                                                      │              ║
║   ┌─────────────────────────────────────────────────▼───────────┐  ║
║   │  Advantage = (r_i - mean(r)) / std(r)  ← GRPO trick         │  ║
║   │  No value network needed — group normalizes itself           │  ║
║   └─────────────────────────────────────────────────┬───────────┘  ║
║                                                      │              ║
║   ┌─────────────────────────────────────────────────▼───────────┐  ║
║   │  Policy gradient update: push up prob of high-advantage     │  ║
║   │  completions, push down low-advantage ones                  │  ║
║   └─────────────────────────────────────────────────────────────┘  ║
║                                                      │              ║
║                           ◀── repeat ────────────────┘              ║
╚══════════════════════════════════════════════════════════════════════╝

KEY INSIGHT: vLLM is ONLY involved in the "rollouts" box above.
It generates N completions per prompt efficiently using PagedAttention.
The gradient update happens in PyTorch (Trainer) — separate process.
""")


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 2: STEP 1 — ROLLOUTS WITH vLLM
# ──────────────────────────────────────────────────────────────────────────────
def generate_rollouts(client, model: str, task: dict) -> list[dict]:
    """
    This is vLLM's job in RL training.

    In real GRPO (DeepSeek-R1, Qwen-3):
    - The vLLM server holds the CURRENT POLICY weights
    - After each gradient update, weights are synced back to vLLM
    - vLLM then generates fresh rollouts from the updated policy

    We use n=G to get G completions in one API call — vLLM batches them.
    """
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": task["prompt"]},
        ],
        n=N_COMPLETIONS,
        temperature=TEMPERATURE,
        max_tokens=32,
    )
    return [
        {"text": choice.message.content.strip(), "answer": task["answer"]}
        for choice in response.choices
    ]


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 3: STEP 2 — REWARD FUNCTION
# ──────────────────────────────────────────────────────────────────────────────

# TODO(human): Implement the reward function below.
#
# This is the heart of RL — it defines what "good" means.
# OpenPipe's entire business is helping companies define good rewards
# for their specific tasks (code correctness, tone, format, accuracy).
#
# Your task: implement compute_reward(completion: dict) -> float
#
# A completion dict has:
#   completion["text"]   — the model's raw output string, e.g. "85" or "the answer is 85"
#   completion["answer"] — the correct integer answer, e.g. 85
#
# Return a float reward. Suggested scale: 0.0 to 1.0
#
# Consider: what if the model says "85!" or "= 85" or "The answer is 85"?
# Should partial credit exist? What about format penalties?
# This is reward design — the most important and hardest part of RL.

def compute_reward(completion: dict) -> float:
    raise NotImplementedError("TODO(human): implement compute_reward in rl_demo.py")


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 4: STEP 3 — GRPO ADVANTAGE COMPUTATION
# ──────────────────────────────────────────────────────────────────────────────
def compute_advantages(rewards: list[float]) -> list[float]:
    """
    GRPO's key innovation over PPO: no value network.

    Instead of training a separate critic to estimate V(s),
    GRPO normalizes rewards WITHIN the group of G completions:
        advantage_i = (reward_i - mean(rewards)) / (std(rewards) + 1e-8)

    This tells the gradient: "relative to the other G outputs for this
    prompt, was this completion better or worse than average?"

    DeepSeek used this to train R1 without a separate reward model — just
    rule-based rewards (math correctness, format) + GRPO.
    """
    if len(rewards) < 2:
        return [0.0] * len(rewards)
    mean_r = statistics.mean(rewards)
    std_r  = statistics.stdev(rewards) if len(set(rewards)) > 1 else 1e-8
    return [(r - mean_r) / (std_r + 1e-8) for r in rewards]


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 5: STEP 4 — POLICY GRADIENT (CONCEPTUAL)
# ──────────────────────────────────────────────────────────────────────────────
def explain_gradient_update(task: dict, completions: list[dict],
                             rewards: list[float], advantages: list[float]):
    """
    In real RL training, the gradient step looks like:
        loss = -mean(advantage_i * log_prob(completion_i | prompt))
    The log_prob comes from running the POLICY MODEL (not vLLM) in
    training mode with the completion as the target sequence.
    Positive advantage → increase log_prob → model outputs this more.
    Negative advantage → decrease log_prob → model outputs this less.
    """
    print(f"\n  Gradient signal for: '{task['prompt']}'")
    for i, (c, r, a) in enumerate(zip(completions, rewards, advantages)):
        direction = "↑ reinforce" if a > 0 else "↓ suppress "
        print(f"    [{i+1}] output='{c['text']:>10}'  reward={r:.2f}  "
              f"advantage={a:+.2f}  → {direction}")


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",   default="http://localhost:8000")
    parser.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--tasks", type=int, default=3,
                        help="How many math tasks to run (max 8)")
    args = parser.parse_args()

    client = OpenAI(base_url=f"{args.url}/v1", api_key="none")

    print_loop_overview()

    print("=" * 70)
    print(f"  Connected to: {args.url}")
    print(f"  Model:        {args.model}")
    print(f"  G (group):    {N_COMPLETIONS} completions per prompt")
    print(f"  Temperature:  {TEMPERATURE}  (diversity for exploration)")
    print("=" * 70)

    total_rewards = []

    for task in TASKS[:args.tasks]:
        print(f"\n{'─'*70}")
        print(f"  PROMPT: {task['prompt']}  (correct answer: {task['answer']})")
        print(f"{'─'*70}")

        # Step 1: rollouts
        print(f"\n  [Step 1] vLLM generating {N_COMPLETIONS} rollouts...")
        completions = generate_rollouts(client, args.model, task)
        for i, c in enumerate(completions):
            print(f"    Completion {i+1}: '{c['text']}'")

        # Step 2: reward
        print(f"\n  [Step 2] Scoring with reward function...")
        rewards = [compute_reward(c) for c in completions]
        for i, (c, r) in enumerate(zip(completions, rewards)):
            print(f"    Completion {i+1}: reward = {r:.2f}")

        # Step 3: advantages
        advantages = compute_advantages(rewards)

        # Step 4: gradient signal
        explain_gradient_update(task, completions, rewards, advantages)

        total_rewards.extend(rewards)

    print(f"\n{'='*70}")
    print(f"  Overall mean reward: {statistics.mean(total_rewards):.3f}")
    print(f"  (Higher = model is already good at these tasks)")
    print(f"  In real training this loop repeats 1000s of times,")
    print(f"  each time updating weights and re-syncing to vLLM.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Accuracy evaluation using IFEval (Instruction Following Evaluation).

Evaluates quantized models on instruction-following capability by testing
whether model outputs satisfy verifiable constraints (e.g., word count,
format requirements, keyword inclusion).

Usage:
    python scripts/evaluate_accuracy.py --base-url http://localhost:8000 --model <model_name>

Environment variables:
    VLLM_BASE_URL: Base URL for vLLM server (default: http://localhost:8000)
    MODEL: Model name for evaluation
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip install requests")
    sys.exit(1)

try:
    from ifeval import Evaluator, instruction_registry, get_default_dataset
except ImportError:
    print("Error: ifeval library required. Install with: pip install ifeval")
    sys.exit(1)


def generate_response(base_url: str, model: str, prompt: str, max_tokens: int = 2048) -> str:
    """Generate response from vLLM server."""
    url = f"{base_url}/v1/chat/completions"
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,  # Deterministic for reproducibility
    }
    
    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        print(f"Error generating response: {e}")
        return ""
    except (KeyError, IndexError) as e:
        print(f"Error parsing response: {e}")
        return ""


def evaluate_ifeval(
    base_url: str,
    model: str,
    num_samples: int = None,
    output_file: str = None,
) -> dict[str, Any]:
    """
    Run IFEval evaluation using the official ifeval library.
    
    Returns:
        Dictionary with evaluation results including:
        - prompt_level_accuracy: % of prompts where ALL instructions were followed
        - instruction_level_accuracy: % of individual instructions followed
    """
    # Load default dataset
    print("Loading IFEval dataset...")
    input_examples = get_default_dataset("en")
    
    # Limit samples if specified
    if num_samples and num_samples < len(input_examples):
        input_examples = input_examples[:num_samples]
    
    print(f"Loaded {len(input_examples)} samples")
    print(f"\nGenerating responses from {model}...")
    print("-" * 60)
    
    # Generate responses for each prompt
    responses = {}
    for i, example in enumerate(input_examples):
        prompt = example.prompt
        response = generate_response(base_url, model, prompt)
        responses[prompt] = response
        
        status = "✓" if response else "✗"
        preview = response[:50].replace("\n", " ") + "..." if response else "FAILED"
        print(f"  [{i+1}/{len(input_examples)}] {status} {preview}")
    
    # Create evaluator and run evaluation
    print("\nRunning IFEval evaluation...")
    evaluator = Evaluator(instruction_registry)
    report, all_outputs = evaluator.evaluate(input_examples, responses)
    
    # Extract metrics from report
    # The report contains eval_results_strict and eval_results_loose dicts
    strict_results = report.get("eval_results_strict", {})
    loose_results = report.get("eval_results_loose", {})
    
    prompt_strict = strict_results.get("prompt_accuracy", 0) * 100
    prompt_loose = loose_results.get("prompt_accuracy", 0) * 100
    inst_strict = strict_results.get("instruction_accuracy", 0) * 100
    inst_loose = loose_results.get("instruction_accuracy", 0) * 100
    
    results = {
        "model": model,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "num_samples": len(input_examples),
        "prompt_level_accuracy": prompt_strict,
        "prompt_level_accuracy_loose": prompt_loose,
        "instruction_level_accuracy": inst_strict,
        "instruction_level_accuracy_loose": inst_loose,
        "raw_report": report,
    }
    
    # Print summary
    print("\n" + "=" * 60)
    print("IFEval Results Summary")
    print("=" * 60)
    print(f"Model: {model}")
    print(f"Samples evaluated: {len(input_examples)}")
    print(f"\nPrompt-level accuracy (strict):      {prompt_strict:.2f}%")
    print(f"Prompt-level accuracy (loose):       {prompt_loose:.2f}%")
    print(f"\nInstruction-level accuracy (strict): {inst_strict:.2f}%")
    print(f"Instruction-level accuracy (loose):  {inst_loose:.2f}%")
    
    # Save results
    if output_file:
        # Convert any non-serializable values
        serializable_results = {
            k: v for k, v in results.items() 
            if k != "raw_report"
        }
        serializable_results["raw_report"] = {
            str(k): float(v) if isinstance(v, (int, float)) else str(v)
            for k, v in report.items()
        }
        
        with open(output_file, "w") as f:
            json.dump(serializable_results, f, indent=2)
        print(f"\nResults saved to: {output_file}")
    
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate model accuracy using IFEval benchmark"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("VLLM_BASE_URL", "http://localhost:8000"),
        help="Base URL for vLLM server",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MODEL", ""),
        help="Model name to evaluate",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=None,
        help="Number of samples to evaluate (default: all ~500)",
    )
    parser.add_argument(
        "--output",
        default="ifeval_results.json",
        help="Output file for results (default: ifeval_results.json)",
    )
    
    args = parser.parse_args()
    
    if not args.model:
        print("Error: Model name required. Use --model or set MODEL env var.")
        sys.exit(1)
    
    # Verify server is accessible
    try:
        resp = requests.get(f"{args.base_url}/health", timeout=10)
        resp.raise_for_status()
        print(f"Connected to vLLM server at {args.base_url}")
    except requests.exceptions.RequestException as e:
        print(f"Error: Cannot connect to vLLM server at {args.base_url}")
        print(f"Details: {e}")
        sys.exit(1)
    
    # Run evaluation
    results = evaluate_ifeval(
        base_url=args.base_url,
        model=args.model,
        num_samples=args.num_samples,
        output_file=args.output,
    )
    
    # Exit with error if accuracy is critically low
    if results["prompt_level_accuracy"] < 10:
        print("\nWarning: Very low accuracy detected!")
        sys.exit(1)
    
    return results


if __name__ == "__main__":
    main()


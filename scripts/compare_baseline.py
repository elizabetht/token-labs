#!/usr/bin/env python3
"""
Compare model accuracy against baseline.

This script compares IFEval accuracy results from a model benchmark run
against established baseline values.

Usage:
    python scripts/compare_baseline.py --results ifeval_results.json --baseline baselines/llama-3.1-8b-instruct.json
    python scripts/compare_baseline.py --results ifeval_results.json --baseline baselines/llama-3.1-8b-instruct.json --update-baseline
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


def load_json(filepath: str) -> Dict[str, Any]:
    """Load JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)


def save_json(filepath: str, data: Dict[str, Any]) -> None:
    """Save JSON file with pretty formatting."""
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)


def compare_accuracy(results: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compare accuracy results against baseline.
    
    Returns:
        Dictionary with comparison results including deltas and pass/fail status.
    """
    comparison = {
        "model": results.get("model", "unknown"),
        "baseline_model": baseline.get("model", "unknown"),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "metrics": {},
        "status": "PASS",
        "summary": []
    }
    
    # Get accuracy values
    result_ifeval = results.get("accuracy", {}).get("ifeval", {}) if "accuracy" in results else results
    baseline_ifeval = baseline.get("accuracy", {}).get("ifeval", {})
    
    # Define metrics to compare
    # Note: baseline uses _strict/_loose suffix, results use base name + _loose
    metrics = [
        ("prompt_level_accuracy", "prompt_level_accuracy_strict", "Prompt-level Accuracy (Strict)", 5.0),
        ("prompt_level_accuracy_loose", "prompt_level_accuracy_loose", "Prompt-level Accuracy (Loose)", 5.0),
        ("instruction_level_accuracy", "instruction_level_accuracy_strict", "Instruction-level Accuracy (Strict)", 5.0),
        ("instruction_level_accuracy_loose", "instruction_level_accuracy_loose", "Instruction-level Accuracy (Loose)", 5.0),
    ]
    
    for result_key, baseline_key, metric_name, threshold in metrics:
        result_value = result_ifeval.get(result_key, 0)
        baseline_value = baseline_ifeval.get(baseline_key)
        
        if baseline_value is None:
            comparison["metrics"][result_key] = {
                "name": metric_name,
                "current": result_value,
                "baseline": None,
                "delta": None,
                "status": "NO_BASELINE",
                "message": "No baseline value available"
            }
            comparison["summary"].append(f"⚠️  {metric_name}: {result_value:.2f}% (no baseline)")
            continue
        
        delta = result_value - baseline_value
        # Check if degradation exceeds threshold
        if delta < -threshold:
            status = "FAIL"
            comparison["status"] = "FAIL"
            symbol = "❌"
        elif abs(delta) <= threshold:
            status = "PASS"
            symbol = "✅"
        else:
            status = "IMPROVED"
            symbol = "🎉"
        
        comparison["metrics"][result_key] = {
            "name": metric_name,
            "current": result_value,
            "baseline": baseline_value,
            "delta": delta,
            "threshold": threshold,
            "status": status,
            "message": f"Current {result_value:.2f}% vs baseline {baseline_value:.2f}% ({'improved' if delta > 0 else 'degraded'} by {abs(delta):.2f}%)"
        }
        
        comparison["summary"].append(
            f"{symbol} {metric_name}: {result_value:.2f}% "
            f"(baseline: {baseline_value:.2f}%, Δ {delta:+.2f}%)"
        )
    
    return comparison


def update_baseline(baseline_path: str, results: Dict[str, Any], run_id: Optional[str] = None) -> None:
    """Update baseline file with new results."""
    baseline = load_json(baseline_path)
    result_ifeval = results.get("accuracy", {}).get("ifeval", {}) if "accuracy" in results else results
    
    # Update baseline values
    baseline["accuracy"]["ifeval"].update({
        "prompt_level_accuracy_strict": result_ifeval.get("prompt_level_accuracy", 0),
        "prompt_level_accuracy_loose": result_ifeval.get("prompt_level_accuracy_loose", 0),
        "instruction_level_accuracy_strict": result_ifeval.get("instruction_level_accuracy", 0),
        "instruction_level_accuracy_loose": result_ifeval.get("instruction_level_accuracy_loose", 0),
        "num_samples": result_ifeval.get("num_samples", 0),
    })
    
    # Update metadata
    baseline["last_updated"] = datetime.utcnow().isoformat() + "Z"
    if run_id:
        baseline["run_id"] = run_id
    
    # Update performance if available in results
    if "prefill" in results:
        baseline["performance"]["prefill_tokens_per_second"] = results["prefill"].get("tokens_per_second")
    if "decode" in results:
        baseline["performance"]["decode_tokens_per_second"] = results["decode"].get("tokens_per_second")
    if "cached" in results:
        baseline["performance"]["cached_tokens_per_second"] = results["cached"].get("tokens_per_second")
    
    save_json(baseline_path, baseline)
    print(f"✅ Baseline updated: {baseline_path}")


def print_comparison(comparison: Dict[str, Any]) -> None:
    """Print comparison results in a readable format."""
    print("\n" + "=" * 80)
    print("🔍 Baseline Comparison Results")
    print("=" * 80)
    print(f"Model: {comparison['model']}")
    print(f"Baseline: {comparison['baseline_model']}")
    print(f"Status: {comparison['status']}")
    print("\n" + "-" * 80)
    print("Metrics:")
    print("-" * 80)
    for summary in comparison["summary"]:
        print(f"  {summary}")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Compare model accuracy against baseline"
    )
    parser.add_argument(
        "--results",
        required=True,
        help="Path to results JSON file (from evaluate_accuracy.py or bench_results.json)",
    )
    parser.add_argument(
        "--baseline",
        required=True,
        help="Path to baseline JSON file",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Update baseline with current results",
    )
    parser.add_argument(
        "--run-id",
        help="Optional run ID to store in baseline",
    )
    parser.add_argument(
        "--output",
        default="comparison_results.json",
        help="Output file for comparison results (default: comparison_results.json)",
    )
    
    args = parser.parse_args()
    
    # Load results and baseline
    try:
        results = load_json(args.results)
        baseline = load_json(args.baseline)
    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON - {e}")
        sys.exit(1)
    
    # Update baseline if requested
    if args.update_baseline:
        update_baseline(args.baseline, results, args.run_id)
        # Print what was updated
        updated_baseline = load_json(args.baseline)
        ifeval = updated_baseline.get("accuracy", {}).get("ifeval", {})
        print("\n" + "=" * 60)
        print("Updated Baseline Values:")
        print("=" * 60)
        print(f"Model: {updated_baseline.get('model', 'unknown')}")
        print(f"Prompt-level (strict): {ifeval.get('prompt_level_accuracy_strict', 0):.2f}%")
        print(f"Prompt-level (loose): {ifeval.get('prompt_level_accuracy_loose', 0):.2f}%")
        print(f"Instruction-level (strict): {ifeval.get('instruction_level_accuracy_strict', 0):.2f}%")
        print(f"Instruction-level (loose): {ifeval.get('instruction_level_accuracy_loose', 0):.2f}%")
        print(f"Samples: {ifeval.get('num_samples', 0)}")
        print("=" * 60 + "\n")
        sys.exit(0)
    
    # Compare results against baseline
    comparison = compare_accuracy(results, baseline)
    
    # Save comparison results
    save_json(args.output, comparison)
    print(f"Comparison results saved to: {args.output}")
    
    # Print comparison
    print_comparison(comparison)
    
    # Exit with error if comparison failed
    if comparison["status"] == "FAIL":
        print("❌ Comparison FAILED: Model accuracy degraded beyond acceptable threshold")
        sys.exit(1)
    elif comparison["status"] == "PASS":
        print("✅ Comparison PASSED: Model accuracy within acceptable range")
    
    return comparison


if __name__ == "__main__":
    main()

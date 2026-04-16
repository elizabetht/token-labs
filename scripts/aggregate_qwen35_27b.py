#!/usr/bin/env python3
"""
Aggregate Qwen3.5-27B benchmark results.
Usage: python3 aggregate_qwen35_27b.py [--results-dir PATH]
"""
import argparse
import glob
import json
import os
from pathlib import Path

RESULTS_DIR = Path(__file__).parent.parent / "results"


def load_results(results_dir: Path):
    records = []
    for path in sorted(glob.glob(str(results_dir / "qwen35-27b-*.json"))):
        try:
            with open(path) as f:
                d = json.load(f)
            records.append((path, d))
        except Exception as e:
            print(f"WARN: could not load {path}: {e}")
    return records


def extract_rows(records):
    """Flatten all benchmark records into rows for ranking."""
    rows = []
    for path, d in records:
        framework = d.get("framework", "?")
        model = d.get("model", "?")
        quant = d.get("quantization", "?")
        technique = d.get("technique", "baseline")
        hardware = d.get("hardware", "")
        for combo_key, combo in d.get("combos", {}).items():
            isl = combo.get("isl", 0)
            osl = combo.get("osl", 0)
            for level in combo.get("levels", []):
                row = {
                    "framework": framework,
                    "model": model,
                    "quantization": quant,
                    "technique": technique,
                    "hardware": hardware,
                    "combo": combo_key,
                    "isl": isl,
                    "osl": osl,
                    "concurrency": level.get("concurrency"),
                    "throughput_tok_s": level.get("throughput_tok_s"),
                    "ttft_p50_ms": level.get("ttft_p50_ms"),
                    "ttft_p99_ms": level.get("ttft_p99_ms"),
                    "itl_p50_ms": level.get("itl_p50_ms"),
                    "itl_p99_ms": level.get("itl_p99_ms"),
                    "dcgm_gpu_util": level.get("dcgm", {}).get("gpu_util_avg_pct"),
                    "dcgm_power_w": level.get("dcgm", {}).get("power_avg_w"),
                    "dcgm_energy_j": level.get("dcgm", {}).get("energy_j"),
                    "source_file": os.path.basename(path),
                }
                rows.append(row)
    return rows


def rank_throughput(rows):
    valid = [r for r in rows if r["throughput_tok_s"] is not None]
    return sorted(valid, key=lambda r: r["throughput_tok_s"], reverse=True)


def rank_latency(rows):
    # Best latency = lowest TTFT p50 at concurrency=1
    valid = [r for r in rows if r["ttft_p50_ms"] is not None and r["concurrency"] == 1]
    return sorted(valid, key=lambda r: r["ttft_p50_ms"])


def print_table(title, rows, key_field, key_label, top_n=10):
    print(f"\n{'='*80}")
    print(f"  {title} (top {top_n})")
    print(f"{'='*80}")
    header = f"{'Rank':>4}  {'Framework':<8}  {'Quant':<10}  {'Technique':<16}  {'Combo':<14}  {'C':>3}  {key_label:>12}  {'GPU%':>5}  {'Power W':>8}"
    print(header)
    print("-" * len(header))
    for i, row in enumerate(rows[:top_n], 1):
        val = row.get(key_field)
        val_str = f"{val:>12.1f}" if val is not None else f"{'N/A':>12}"
        gpu = row.get("dcgm_gpu_util")
        gpu_str = f"{gpu:>5.1f}" if gpu is not None else f"{'N/A':>5}"
        pwr = row.get("dcgm_power_w")
        pwr_str = f"{pwr:>8.1f}" if pwr is not None else f"{'N/A':>8}"
        print(f"{i:>4}  {row['framework']:<8}  {row['quantization']:<10}  {row['technique']:<16}  {row['combo']:<14}  {row['concurrency']:>3}  {val_str}  {gpu_str}  {pwr_str}")


def save_summary(rows, results_dir):
    by_throughput = rank_throughput(rows)
    by_latency = rank_latency(rows)

    summary = {
        "best_throughput": by_throughput[:5] if by_throughput else [],
        "best_latency": by_latency[:5] if by_latency else [],
        "total_rows": len(rows),
    }
    out_path = results_dir / "qwen35-27b-summary.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved to {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    records = load_results(results_dir)
    if not records:
        print(f"No results found in {results_dir}")
        return

    print(f"Loaded {len(records)} result files")
    rows = extract_rows(records)
    print(f"Extracted {len(rows)} data points")

    by_throughput = rank_throughput(rows)
    by_latency = rank_latency(rows)

    print_table("BEST THROUGHPUT (all combos x concurrency)", by_throughput, "throughput_tok_s", "Throughput tok/s")
    print_table("BEST LATENCY -- TTFT p50 @ concurrency=1", by_latency, "ttft_p50_ms", "TTFT p50 (ms)")

    # Also print per-combo throughput winners
    combos = sorted(set(r["combo"] for r in rows))
    for combo in combos:
        combo_rows = [r for r in by_throughput if r["combo"] == combo]
        if combo_rows:
            best = combo_rows[0]
            print(f"\nBest throughput {combo}: {best['framework']}/{best['quantization']}/{best['technique']} "
                  f"c={best['concurrency']} -> {best['throughput_tok_s']:.1f} tok/s")

    save_summary(rows, results_dir)


if __name__ == "__main__":
    main()

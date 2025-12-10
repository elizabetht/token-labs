#!/usr/bin/env python
"""
Update index.html with benchmark pricing results.

Reads bench_results.json and updates the MODELS configuration in docs/index.html
with the calculated cost_per_million_tokens for input (prefill) and output (decode).

Usage:
    python scripts/update_pricing.py [--results bench_results.json] [--html docs/index.html]

Environment variables (optional):
    BENCH_RESULTS_PATH: Path to benchmark results JSON (default: bench_results.json)
    HTML_PATH: Path to index.html (default: docs/index.html)
"""

import argparse
import json
import os
import re
import sys


def load_benchmark_results(results_path: str) -> dict:
    """Load benchmark results from JSON file."""
    with open(results_path, "r") as f:
        return json.load(f)


def extract_model_key_from_backend_name(backend_name: str, html_content: str) -> str | None:
    """
    Find the model key in MODELS that matches the backend name.
    
    The benchmark uses backendName (e.g., 'meta-llama/Llama-3.1-8B-Instruct')
    but index.html might use a shorter key (e.g., 'llama-8b-instruct').
    
    This function searches the HTML for a model entry where backendName matches.
    """
    # First, try to find an exact match for backendName in the HTML
    # Pattern: "some-key": { ... backendName: "backend_name" ...
    pattern = r'"([^"]+)":\s*\{[^}]*backendName:\s*"' + re.escape(backend_name) + r'"'
    match = re.search(pattern, html_content)
    if match:
        return match.group(1)
    
    # If no exact match, try matching by model identifier parts
    # e.g., "llama-8b-instruct" should match "meta-llama/Llama-3.1-8B-Instruct"
    backend_lower = backend_name.lower()
    
    # Extract all model keys from MODELS
    model_keys_pattern = r'const MODELS = \{([^;]+)\};'
    models_match = re.search(model_keys_pattern, html_content, re.DOTALL)
    if not models_match:
        return None
    
    models_block = models_match.group(1)
    key_pattern = r'"([^"]+)":\s*\{'
    keys = re.findall(key_pattern, models_block)
    
    for key in keys:
        # Check if key parts appear in backend name
        key_parts = key.lower().replace("-", " ").split()
        if all(part in backend_lower for part in key_parts if len(part) > 2):
            return key
    
    return None


def update_model_pricing(html_content: str, model_key: str, input_price: float, output_price: float, cached_input_price: float = None) -> str:
    """
    Update the inputPricePerM, outputPricePerM, and cachedInputPricePerM for a specific model in the HTML.
    """
    # Pattern to find the model block and its pricing
    # We need to find the model key and update its pricing values
    
    # Find the model block
    model_pattern = rf'"{re.escape(model_key)}":\s*\{{[^}}]+\}}'
    model_match = re.search(model_pattern, html_content, re.DOTALL)
    
    if not model_match:
        print(f"Warning: Could not find model '{model_key}' in HTML")
        return html_content
    
    model_block = model_match.group(0)
    
    # Update inputPricePerM
    updated_block = re.sub(
        r'inputPricePerM:\s*[\d.]+',
        f'inputPricePerM: {input_price:.2f}',
        model_block
    )
    
    # Update outputPricePerM
    updated_block = re.sub(
        r'outputPricePerM:\s*[\d.]+',
        f'outputPricePerM: {output_price:.2f}',
        updated_block
    )
    
    # Add or update cachedInputPricePerM if provided
    if cached_input_price is not None:
        if 'cachedInputPricePerM:' in updated_block:
            # Update existing cachedInputPricePerM
            updated_block = re.sub(
                r'cachedInputPricePerM:\s*[\d.]+',
                f'cachedInputPricePerM: {cached_input_price:.4f}',
                updated_block
            )
        else:
            # Add cachedInputPricePerM after inputPricePerM
            updated_block = re.sub(
                r'(inputPricePerM:\s*[\d.]+,)',
                f'\\1\n        cachedInputPricePerM: {cached_input_price:.4f},',
                updated_block
            )
    
    # Replace the old block with the updated one
    html_content = html_content.replace(model_block, updated_block)
    
    return html_content


def main():
    parser = argparse.ArgumentParser(
        description="Update index.html with benchmark pricing results"
    )
    parser.add_argument(
        "--results",
        default=os.environ.get("BENCH_RESULTS_PATH", "bench_results.json"),
        help="Path to benchmark results JSON file",
    )
    parser.add_argument(
        "--html",
        default=os.environ.get("HTML_PATH", "docs/index.html"),
        help="Path to index.html file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without writing to file",
    )
    args = parser.parse_args()

    # Load benchmark results
    if not os.path.exists(args.results):
        print(f"Error: Benchmark results file not found: {args.results}")
        sys.exit(1)

    results = load_benchmark_results(args.results)
    print(f"Loaded benchmark results from: {args.results}")
    print(f"  Model: {results.get('model', 'unknown')}")
    
    # Extract pricing from results
    prefill = results.get("prefill", {})
    decode = results.get("decode", {})
    cached = results.get("cached", {})
    
    input_price = prefill.get("cost_per_million_tokens", 0)
    output_price = decode.get("cost_per_million_tokens", 0)
    cached_input_price = cached.get("cost_per_million_tokens", 0)
    
    print(f"  Input price (per 1M tokens):  ${input_price:.2f}")
    print(f"  Cached input price (per 1M tokens): ${cached_input_price:.2f}")
    print(f"  Output price (per 1M tokens): ${output_price:.2f}")

    # Load HTML file
    if not os.path.exists(args.html):
        print(f"Error: HTML file not found: {args.html}")
        sys.exit(1)

    with open(args.html, "r") as f:
        html_content = f.read()

    # Find the model key that matches the benchmark
    backend_name = results.get("model", "")
    model_key = extract_model_key_from_backend_name(backend_name, html_content)
    
    if not model_key:
        print(f"Error: Could not find model key for backend name: {backend_name}")
        print("Please ensure the model exists in the MODELS configuration in index.html")
        sys.exit(1)

    print(f"  Matched model key: {model_key}")

    # Update the HTML
    updated_html = update_model_pricing(html_content, model_key, input_price, output_price, cached_input_price)

    if html_content == updated_html:
        print("No changes made (pricing already up to date or model not found)")
        return

    if args.dry_run:
        print("\n=== Dry run - changes that would be made ===")
        # Show the updated model block
        model_pattern = rf'"{re.escape(model_key)}":\s*\{{[^}}]+\}}'
        match = re.search(model_pattern, updated_html, re.DOTALL)
        if match:
            print(match.group(0))
        return

    # Write updated HTML
    with open(args.html, "w") as f:
        f.write(updated_html)

    print(f"\nUpdated {args.html} with new pricing for model '{model_key}'")


if __name__ == "__main__":
    main()

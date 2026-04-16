#!/usr/bin/env bash
# orchestrate_qwen35_27b.sh — Full test matrix for Qwen3.5-27B benchmarking.
#
# Usage:
#   PHASE=A ./orchestrate_qwen35_27b.sh           # Framework × model baselines
#   PHASE=B BEST_FRAMEWORK=vllm BEST_QUANT=fp8 \
#           BEST_MODEL=Qwen/Qwen3.5-27B-FP8 \
#           ./orchestrate_qwen35_27b.sh           # Technique sweep on best winner
#   PHASE=C ...same env vars...                   # Best combo runs
#   PHASE=ALL ./orchestrate_qwen35_27b.sh         # All phases sequentially
set -euo pipefail

REPO="/home/nvidia/src/github.com/elizabetht/token-labs"
NAMESPACE="token-labs"
RESULTS_DIR="$REPO/results"
SCRIPTS_DIR="$REPO/scripts/qwen35-27b"
DEPLOY_DIR="$REPO/deploy/models/qwen35-27b"
DATE=$(date +%Y-%m-%d)

# Phase tracking: set PHASE=A (default), B, C, or ALL via env var
PHASE=${PHASE:-A}

# For Phase B/C: which framework+model+quant won Phase A (set via env)
BEST_FRAMEWORK=${BEST_FRAMEWORK:-vllm}
BEST_QUANT=${BEST_QUANT:-fp8}
BEST_MODEL=${BEST_MODEL:-Qwen/Qwen3.5-27B-FP8}

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── Pod lifecycle helpers ────────────────────────────────────────────────────

wait_pod_ready() {
    local pod=$1 container=$2 timeout=${3:-900}
    log "Waiting for $pod/$container (up to ${timeout}s)..."
    local deadline=$((SECONDS + timeout))
    while [[ $SECONDS -lt $deadline ]]; do
        local phase
        phase=$(kubectl get pod -n "$NAMESPACE" "$pod" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        if [[ "$phase" == "Failed" ]]; then
            log "ERROR: pod $pod entered Failed state"
            kubectl logs -n "$NAMESPACE" "$pod" -c "$container" --tail=50 || true
            return 1
        fi
        if kubectl exec -n "$NAMESPACE" "$pod" -c "$container" -- \
               curl -sf http://localhost:8000/health &>/dev/null; then
            log "$pod is ready"
            return 0
        fi
        local remaining=$(( deadline - SECONDS ))
        log "  Not ready yet (phase=$phase, ${remaining}s remaining)..."
        sleep 20
    done
    log "Timeout waiting for $pod"
    return 1
}

teardown_pod() {
    local pod=$1
    log "Tearing down $pod..."
    kubectl delete pod -n "$NAMESPACE" "$pod" --ignore-not-found --wait=true --timeout=120s || true
    sleep 30  # allow GPU memory to release
}

# ── Core benchmark runner ─────────────────────────────────────────────────────
# run_benchmark manifest pod container framework model quant [technique]
run_benchmark() {
    local manifest=$1
    local pod=$2
    local container=$3
    local framework=$4
    local model=$5
    local quant=$6
    local technique=${7:-baseline}

    local output="$RESULTS_DIR/qwen35-27b-${framework}-${quant}-${technique}-${DATE}.json"

    # Skip if already complete
    if [[ -f "$output" ]]; then
        local progress done total
        progress=$(python3 -c "
import json, sys
try:
    d = json.load(open('$output'))
    print(d.get('progress', '0/0'))
except Exception:
    print('0/0')
" 2>/dev/null || echo "0/0")
        done=$(echo "$progress" | cut -d/ -f1)
        total=$(echo "$progress" | cut -d/ -f2)
        if [[ "$done" == "$total" && "$total" != "0" ]]; then
            log "SKIP: $output already complete ($progress)"
            return 0
        fi
    fi

    log "=== START: framework=$framework quant=$quant technique=$technique ==="
    teardown_pod "$pod" || true

    # For technique variants, inject extra args into the manifest via sed substitution.
    # Pod manifests are expected to have EXTRA_<FRAMEWORK>_ARGS: "" env vars.
    local extra_args=""
    local fw_upper
    fw_upper=$(echo "$framework" | tr '[:lower:]' '[:upper:]')

    if [[ "$technique" != "baseline" ]]; then
        extra_args=$(python3 "$SCRIPTS_DIR/bench.py" \
            --print-technique-flags "$framework" "$technique" 2>/dev/null \
            | python3 -c "import json,sys; flags=json.load(sys.stdin); print(' '.join(flags))" \
            || echo "")
    fi

    if [[ -n "$extra_args" ]]; then
        local tmp
        tmp=$(mktemp --suffix=.yaml)
        # Replace the placeholder value for the EXTRA_*_ARGS env var in the manifest
        sed "s|\(EXTRA_${fw_upper}_ARGS.*value:\s*\)\"\"|\1\"${extra_args}\"|g" \
            "$manifest" > "$tmp"
        kubectl apply -f "$tmp" -n "$NAMESPACE"
        rm -f "$tmp"
        log "  Applied manifest with EXTRA_${fw_upper}_ARGS='${extra_args}'"
    else
        kubectl apply -f "$manifest" -n "$NAMESPACE"
    fi

    if ! wait_pod_ready "$pod" "$container" 1800; then
        log "ERROR: $pod not ready, skipping"
        teardown_pod "$pod"
        return 1
    fi

    log "Running benchmark: $framework/$quant/$technique"
    python3 "$SCRIPTS_DIR/bench.py" \
        --framework   "$framework" \
        --model       "$model" \
        --quantization "$quant" \
        --technique   "$technique" \
        --pod         "$pod" \
        --container   "$container" \
        --output      "$output" \
        --num-warmups 10

    teardown_pod "$pod"
    log "=== DONE: $output ==="
}

# ── Phase A: Framework × Model Baselines ─────────────────────────────────────
run_phase_a() {
    log "=== PHASE A: Framework × Model Baselines ==="

    # vLLM × 3 quantizations
    run_benchmark "$DEPLOY_DIR/pods-vllm-bf16.yaml"      qwen35-27b-vllm-bf16-leader       vllm   vllm   "Qwen/Qwen3.5-27B"           bf16      baseline
    run_benchmark "$DEPLOY_DIR/pods-vllm-fp8.yaml"       qwen35-27b-vllm-fp8-leader        vllm   vllm   "Qwen/Qwen3.5-27B-FP8"       fp8       baseline
    run_benchmark "$DEPLOY_DIR/pods-vllm-gptq-int4.yaml" qwen35-27b-vllm-gptq-int4-leader  vllm   vllm   "Qwen/Qwen3.5-27B-GPTQ-Int4" gptq-int4 baseline

    # SGLang × 3 quantizations
    run_benchmark "$DEPLOY_DIR/pods-sglang-bf16.yaml"      qwen35-27b-sglang-bf16-leader       sglang sglang "Qwen/Qwen3.5-27B"           bf16      baseline
    run_benchmark "$DEPLOY_DIR/pods-sglang-fp8.yaml"       qwen35-27b-sglang-fp8-leader        sglang sglang "Qwen/Qwen3.5-27B-FP8"       fp8       baseline
    run_benchmark "$DEPLOY_DIR/pods-sglang-gptq-int4.yaml" qwen35-27b-sglang-gptq-int4-leader  sglang sglang "Qwen/Qwen3.5-27B-GPTQ-Int4" gptq-int4 baseline

    # TRT-LLM × 3 quantizations
    run_benchmark "$DEPLOY_DIR/pods-trtllm-bf16.yaml"      qwen35-27b-trtllm-bf16-leader       trtllm trtllm "Qwen/Qwen3.5-27B"           bf16      baseline
    run_benchmark "$DEPLOY_DIR/pods-trtllm-fp8.yaml"       qwen35-27b-trtllm-fp8-leader        trtllm trtllm "Qwen/Qwen3.5-27B-FP8"       fp8       baseline
    run_benchmark "$DEPLOY_DIR/pods-trtllm-gptq-int4.yaml" qwen35-27b-trtllm-gptq-int4-leader  trtllm trtllm "Qwen/Qwen3.5-27B-GPTQ-Int4" gptq-int4 baseline
}

# ── Phase B: Technique Sweep on best Phase A winner ──────────────────────────
run_phase_b() {
    log "=== PHASE B: Technique Sweep on best=$BEST_FRAMEWORK/$BEST_QUANT ==="
    local manifest="$DEPLOY_DIR/pods-${BEST_FRAMEWORK}-${BEST_QUANT}.yaml"
    local pod_base="qwen35-27b-${BEST_FRAMEWORK}-${BEST_QUANT}"

    local techniques
    if [[ "$BEST_FRAMEWORK" == "vllm" ]]; then
        techniques="no-cuda-graph kv-fp8 lmcache-8g lmcache-20g spec-ngram spec-mtp"
    elif [[ "$BEST_FRAMEWORK" == "sglang" ]]; then
        techniques="no-cuda-graph kv-fp8 spec-ngram spec-mtp overlap-schedule"
    else
        techniques="kv-fp8"
    fi

    for technique in $techniques; do
        run_benchmark \
            "$manifest" \
            "${pod_base}-leader" \
            "$BEST_FRAMEWORK" \
            "$BEST_FRAMEWORK" \
            "$BEST_MODEL" \
            "$BEST_QUANT" \
            "$technique"
    done
}

# ── Phase C: Best Combination ────────────────────────────────────────────────
run_phase_c() {
    log "=== PHASE C: Best Combination ==="
    local manifest="$DEPLOY_DIR/pods-${BEST_FRAMEWORK}-${BEST_QUANT}.yaml"
    local pod_base="qwen35-27b-${BEST_FRAMEWORK}-${BEST_QUANT}"

    if [[ "$BEST_FRAMEWORK" == "vllm" ]]; then
        run_benchmark "$manifest" "${pod_base}-leader" "$BEST_FRAMEWORK" "$BEST_FRAMEWORK" "$BEST_MODEL" "$BEST_QUANT" "kv-fp8+lmcache"
        run_benchmark "$manifest" "${pod_base}-leader" "$BEST_FRAMEWORK" "$BEST_FRAMEWORK" "$BEST_MODEL" "$BEST_QUANT" "kv-fp8+spec"
    elif [[ "$BEST_FRAMEWORK" == "sglang" ]]; then
        run_benchmark "$manifest" "${pod_base}-leader" "$BEST_FRAMEWORK" "$BEST_FRAMEWORK" "$BEST_MODEL" "$BEST_QUANT" "kv-fp8+overlap"
        run_benchmark "$manifest" "${pod_base}-leader" "$BEST_FRAMEWORK" "$BEST_FRAMEWORK" "$BEST_MODEL" "$BEST_QUANT" "kv-fp8+spec"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    mkdir -p "$RESULTS_DIR"

    log "Starting Qwen3.5-27B benchmark orchestration (PHASE=$PHASE)"
    log "Results → $RESULTS_DIR"

    case "$PHASE" in
        A)   run_phase_a ;;
        B)   run_phase_b ;;
        C)   run_phase_c ;;
        ALL) run_phase_a; run_phase_b; run_phase_c ;;
        *)   echo "Usage: PHASE={A|B|C|ALL} $0"; exit 1 ;;
    esac

    log "All done. Run: python3 $SCRIPTS_DIR/aggregate.py"
}

main "$@"

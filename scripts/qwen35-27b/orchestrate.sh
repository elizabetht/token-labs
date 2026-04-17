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

# ── spark-02 production pod management ───────────────────────────────────────
SPARK02_PRODUCTION_PODS="deepseek-r1-7b-vllm-leader llama-31-8b-sglang-leader qwen25-7b-trtllm-spark02-leader"

stop_spark02_production() {
    log "Stopping spark-02 production pods for exclusive GPU access..."
    for pod in $SPARK02_PRODUCTION_PODS; do
        kubectl delete pod -n "$NAMESPACE" "$pod" --ignore-not-found --wait=false 2>/dev/null || true
    done
    # Wait for all to terminate
    for pod in $SPARK02_PRODUCTION_PODS; do
        kubectl wait --for=delete pod/"$pod" -n "$NAMESPACE" --timeout=120s 2>/dev/null || true
    done
    log "spark-02 production pods stopped"
}

restore_spark02_production() {
    log "Restoring spark-02 production pods..."
    kubectl apply -f "$REPO/deploy/models/deepseek-r1-7b/pods-deepseek-r1.yaml" -n "$NAMESPACE" || true
    kubectl apply -f "$REPO/deploy/models/llama-31-8b/pods-llama-sglang.yaml" -n "$NAMESPACE" || true
    kubectl apply -f "$REPO/deploy/models/qwen25-7b/pods-trtllm-spark02.yaml" -n "$NAMESPACE" || true
    log "spark-02 production pods restored"
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

    # Skip if any date-variant of this run is already complete
    local existing
    existing=$(ls "$RESULTS_DIR"/qwen35-27b-${framework}-${quant}-${technique}-[0-9]*.json 2>/dev/null | head -1 || true)
    if [[ -n "$existing" ]]; then
        local progress done total
        progress=$(python3 -c "
import json, sys
try:
    d = json.load(open('$existing'))
    print(d.get('progress', '0/0'))
except Exception:
    print('0/0')
" 2>/dev/null || echo "0/0")
        done=$(echo "$progress" | cut -d/ -f1)
        total=$(echo "$progress" | cut -d/ -f2)
        if [[ "$done" == "$total" && "$total" != "0" ]]; then
            log "SKIP: $existing already complete ($progress)"
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

# ── spark-02 benchmark runner ─────────────────────────────────────────────────
# run_benchmark_spark02 manifest pod container framework model quant [technique]
# Identical to run_benchmark — pod manifests already have nodeSelector: spark-02.
SPARK02_HOST="nvidia@192.168.1.77"

run_benchmark_spark02() {
    local manifest=$1
    local pod=$2
    local container=$3
    local framework=$4
    local model=$5
    local quant=$6
    local technique=${7:-baseline}

    local output="$RESULTS_DIR/qwen35-27b-${framework}-${quant}-${technique}-spark02-${DATE}.json"

    # Skip if any date-variant of this spark02 run is already complete
    local existing
    existing=$(ls "$RESULTS_DIR"/qwen35-27b-${framework}-${quant}-${technique}-spark02-[0-9]*.json 2>/dev/null | head -1 || true)
    if [[ -n "$existing" ]]; then
        local progress done total
        progress=$(python3 -c "
import json, sys
try:
    d = json.load(open('$existing'))
    print(d.get('progress', '0/0'))
except Exception:
    print('0/0')
" 2>/dev/null || echo "0/0")
        done=$(echo "$progress" | cut -d/ -f1)
        total=$(echo "$progress" | cut -d/ -f2)
        if [[ "$done" == "$total" && "$total" != "0" ]]; then
            log "SKIP: $existing already complete ($progress)"
            return 0
        fi
    fi

    log "=== START (spark-02): framework=$framework quant=$quant technique=$technique ==="
    teardown_pod "$pod" || true

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

    log "Running benchmark (spark-02): $framework/$quant/$technique"
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
    log "=== DONE (spark-02): $output ==="
}

# ── Phase A: Framework × Model Baselines ─────────────────────────────────────
run_phase_a() {
    log "=== PHASE A: Framework × Model Baselines (spark-01: vLLM | spark-02: SGLang+TRT-LLM) ==="

    stop_spark02_production

    # spark-02: SGLang + TRT-LLM (background)
    (
        run_benchmark_spark02 "$DEPLOY_DIR/pods-sglang-bf16-spark02.yaml"      qwen35-27b-sglang-bf16-spark02-leader      sglang  sglang  "Qwen/Qwen3.5-27B"           bf16      baseline || true
        run_benchmark_spark02 "$DEPLOY_DIR/pods-sglang-fp8-spark02.yaml"       qwen35-27b-sglang-fp8-spark02-leader       sglang  sglang  "Qwen/Qwen3.5-27B-FP8"       fp8       baseline || true
        run_benchmark_spark02 "$DEPLOY_DIR/pods-sglang-gptq-int4-spark02.yaml" qwen35-27b-sglang-gptq-int4-spark02-leader sglang  sglang  "Qwen/Qwen3.5-27B-GPTQ-Int4" gptq-int4 baseline || true
        run_benchmark_spark02 "$DEPLOY_DIR/pods-trtllm-bf16-spark02.yaml"      qwen35-27b-trtllm-bf16-spark02-leader      trtllm  trtllm  "Qwen/Qwen3.5-27B"           bf16      baseline || true
        run_benchmark_spark02 "$DEPLOY_DIR/pods-trtllm-fp8-spark02.yaml"       qwen35-27b-trtllm-fp8-spark02-leader       trtllm  trtllm  "Qwen/Qwen3.5-27B-FP8"       fp8       baseline || true
        run_benchmark_spark02 "$DEPLOY_DIR/pods-trtllm-gptq-int4-spark02.yaml" qwen35-27b-trtllm-gptq-int4-spark02-leader trtllm  trtllm  "Qwen/Qwen3.5-27B-GPTQ-Int4" gptq-int4 baseline || true
    ) &
    SPARK02_PID=$!

    # spark-01: vLLM (foreground)
    run_benchmark "$DEPLOY_DIR/pods-vllm-bf16.yaml"      qwen35-27b-vllm-bf16-spark01-leader      vllm vllm "Qwen/Qwen3.5-27B"           bf16      baseline
    run_benchmark "$DEPLOY_DIR/pods-vllm-fp8.yaml"       qwen35-27b-vllm-fp8-spark01-leader       vllm vllm "Qwen/Qwen3.5-27B-FP8"       fp8       baseline
    run_benchmark "$DEPLOY_DIR/pods-vllm-gptq-int4.yaml" qwen35-27b-vllm-gptq-int4-spark01-leader vllm vllm "Qwen/Qwen3.5-27B-GPTQ-Int4" gptq-int4 baseline

    # Wait for spark-02 to finish (tolerate early exit from pod failures)
    log "Waiting for spark-02 benchmarks to complete..."
    wait $SPARK02_PID || true
    log "Both nodes complete"

    restore_spark02_production
}

# ── Phase B: Technique Sweep on best Phase A winner ──────────────────────────
run_phase_b() {
    log "=== PHASE B: Technique Sweep on best=$BEST_FRAMEWORK/$BEST_QUANT ==="
    local manifest="$DEPLOY_DIR/pods-${BEST_FRAMEWORK}-${BEST_QUANT}.yaml"
    local node_suffix; [[ "$BEST_FRAMEWORK" == "vllm" ]] && node_suffix="spark01" || node_suffix="spark02"
    local pod_base="qwen35-27b-${BEST_FRAMEWORK}-${BEST_QUANT}-${node_suffix}"

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
    local node_suffix; [[ "$BEST_FRAMEWORK" == "vllm" ]] && node_suffix="spark01" || node_suffix="spark02"
    local pod_base="qwen35-27b-${BEST_FRAMEWORK}-${BEST_QUANT}-${node_suffix}"

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

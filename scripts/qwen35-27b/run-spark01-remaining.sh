#!/usr/bin/env bash
# Run trtllm-bf16 and sglang-gptq-int4 on spark-01 in parallel.
set -euo pipefail
REPO="/home/nvidia/src/github.com/elizabetht/token-labs"
NS="token-labs"
RESULTS="$REPO/results"
DEPLOY="$REPO/deploy/models/qwen35-27b"
SCRIPTS="$REPO/scripts/qwen35-27b"
DATE=$(date +%Y-%m-%d)
log() { echo "[$(date +%H:%M:%S)] $*"; }

wait_ready() {
    local pod=$1 container=$2 timeout=${3:-1800}
    local deadline=$((SECONDS + timeout))
    log "Waiting for $pod ($container) up to ${timeout}s..."
    while [[ $SECONDS -lt $deadline ]]; do
        local phase; phase=$(kubectl get pod -n "$NS" "$pod" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        [[ "$phase" == "Failed" ]] && { log "ERROR: $pod Failed"; kubectl logs -n "$NS" "$pod" -c "$container" --tail=30 || true; return 1; }
        kubectl exec -n "$NS" "$pod" -c "$container" -- curl -sf http://localhost:8000/health &>/dev/null && { log "$pod ready"; return 0; }
        log "  $pod not ready (phase=$phase, $((deadline - SECONDS))s left)..."
        sleep 20
    done
    log "TIMEOUT: $pod"; return 1
}

teardown() { kubectl delete pod -n "$NS" "$1" --ignore-not-found --wait=true --timeout=120s || true; sleep 30; }

run_one() {
    local manifest=$1 pod=$2 container=$3 framework=$4 model=$5 quant=$6
    local output="$RESULTS/qwen35-27b-${framework}-${quant}-baseline-spark01-${DATE}.json"
    # skip if already complete
    local existing; existing=$(ls "$RESULTS"/qwen35-27b-${framework}-${quant}-baseline-spark01-[0-9]*.json 2>/dev/null | head -1 || true)
    if [[ -n "$existing" ]]; then
        local prog; prog=$(python3 -c "import json; d=json.load(open('$existing')); print(d.get('progress','0/0'))" 2>/dev/null || echo "0/0")
        local done total; done=$(echo "$prog"|cut -d/ -f1); total=$(echo "$prog"|cut -d/ -f2)
        [[ "$done" == "$total" && "$total" != "0" ]] && { log "SKIP $existing ($prog)"; return 0; }
    fi
    log "=== START spark-01: $framework/$quant ==="
    teardown "$pod" || true
    kubectl apply -f "$manifest" -n "$NS"
    if ! wait_ready "$pod" "$container" 1800; then
        teardown "$pod"; log "FAILED: $framework/$quant on spark-01"; return 1
    fi
    python3 "$SCRIPTS/bench.py" --framework "$framework" --model "$model" --quantization "$quant" \
        --technique baseline --pod "$pod" --container "$container" --output "$output" --num-warmups 10
    teardown "$pod"
    log "=== DONE spark-01: $output ==="
}

(run_one "$DEPLOY/pods-trtllm-bf16.yaml"      qwen35-27b-trtllm-bf16-spark01-leader      trtllm trtllm "Qwen/Qwen3.5-27B"           bf16      || true) &
(run_one "$DEPLOY/pods-sglang-gptq-int4.yaml" qwen35-27b-sglang-gptq-int4-spark01-leader sglang sglang "Qwen/Qwen3.5-27B-GPTQ-Int4" gptq-int4 || true) &
wait
log "All spark-01 jobs done."

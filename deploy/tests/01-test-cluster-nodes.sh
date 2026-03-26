#!/bin/bash
# Post-bootstrap test: Verify all nodes are Ready
# Usage: ./01-test-cluster-nodes.sh

set -e

echo "=== Cluster Node Readiness Test ==="
echo

echo "Checking cluster nodes..."
kubectl get nodes -o wide

echo
echo "Waiting for all nodes to be Ready..."
timeout=300
elapsed=0
interval=5

while [ $elapsed -lt $timeout ]; do
    ready_count=$(kubectl get nodes --no-headers | grep -c " Ready" || true)
    total_count=$(kubectl get nodes --no-headers | wc -l)

    echo "Ready nodes: $ready_count / $total_count"

    if [ "$ready_count" -eq "$total_count" ] && [ "$total_count" -gt 0 ]; then
        echo
        echo "✅ SUCCESS: All $total_count nodes are Ready!"
        echo
        kubectl get nodes
        exit 0
    fi

    sleep $interval
    elapsed=$((elapsed + interval))
done

echo
echo "❌ FAIL: Not all nodes became Ready within ${timeout}s"
kubectl get nodes
exit 1

#!/usr/bin/env bash
set -euo pipefail

# Defaults for your cluster; override as needed:
#   GPU_NS=gpu-operator NODES="spark-01 spark-02" MODE=shared ./enable-time-slicing.sh
GPU_NS="${GPU_NS:-gpu-operator}"
NODES="${NODES:-spark-01 spark-02}"
MODE="${MODE:-shared}"   # "shared" (gpu.shared) or "gpu" (keep nvidia.com/gpu)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$MODE" in
  shared) CONFIG="$SCRIPT_DIR/time-slicing-gpu-shared.yaml" ;;
  gpu)    CONFIG="$SCRIPT_DIR/time-slicing-gpu.yaml" ;;
  *) echo "MODE must be 'shared' or 'gpu'"; exit 1 ;;
esac

echo "==> Applying time-slicing ConfigMap ($MODE) into namespace: $GPU_NS"
# apply file but rewrite namespace if GPU_NS differs from what's in YAML
# simplest: use kubectl -n and rely on metadata.namespace in yaml matching; enforce by patching file would be overkill
kubectl apply -n "$GPU_NS" -f "$CONFIG"

echo "==> Patching ClusterPolicy to use the ConfigMap (time-slicing-config / default=any)"
kubectl patch clusterpolicies.nvidia.com/cluster-policy \
  --type merge \
  -p '{"spec":{"devicePlugin":{"config":{"name":"time-slicing-config","default":"any"}}}}'

echo "==> Labeling nodes to use device-plugin config: any"
for n in $NODES; do
  kubectl label node "$n" nvidia.com/device-plugin.config=any --overwrite
done

echo "==> Restarting device-plugin daemonset"
kubectl -n "$GPU_NS" rollout restart ds/nvidia-device-plugin-daemonset

echo "==> Verifying allocatable GPU resources"
kubectl get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\t"}}{{index .status.allocatable "nvidia.com/gpu"}}{{"\t"}}{{with index .status.allocatable "nvidia.com/gpu.shared"}}{{.}}{{else}}0{{end}}{{"\n"}}{{end}}'

echo
echo "Done."
echo "If MODE=shared: expect spark nodes to show gpu=0 and gpu.shared=<replicas>."
echo "If MODE=gpu:    expect spark nodes to show gpu=<replicas> and gpu.shared=0."
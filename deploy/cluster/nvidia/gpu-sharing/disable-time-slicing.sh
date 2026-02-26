#!/usr/bin/env bash
set -euo pipefail

GPU_NS="${GPU_NS:-gpu-operator}"
NODES="${NODES:-spark-01 spark-02}"

echo "==> Removing device-plugin config label from nodes (revert to default config)"
for n in $NODES; do
  kubectl label node "$n" nvidia.com/device-plugin.config- || true
done

echo "==> Restarting device-plugin daemonset"
kubectl -n "$GPU_NS" rollout restart ds/nvidia-device-plugin-daemonset

echo "==> Verifying allocatable GPU resources"
kubectl get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\t"}}{{index .status.allocatable "nvidia.com/gpu"}}{{"\t"}}{{with index .status.allocatable "nvidia.com/gpu.shared"}}{{.}}{{else}}0{{end}}{{"\n"}}{{end}}'

echo "Done."
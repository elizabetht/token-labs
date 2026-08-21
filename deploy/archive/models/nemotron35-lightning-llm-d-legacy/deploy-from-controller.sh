#!/usr/bin/env bash
set -euo pipefail

GPU_NS="${GPU_NS:-gpu-operator}"
MODEL_NS="${MODEL_NS:-token-labs}"
NODE="${NODE:-spark-01}"

command -v kubectl >/dev/null
command -v helmfile >/dev/null
kubectl get node "$NODE" >/dev/null
kubectl get clusterpolicy cluster-policy >/dev/null
kubectl get crd inferencepools.inference.networking.k8s.io >/dev/null

kubectl create namespace "$MODEL_NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/infrastructure/cluster/nvidia/gpu-sharing/time-slicing-gpu-shared.yaml
kubectl patch clusterpolicy cluster-policy --type merge \
  -p '{"spec":{"devicePlugin":{"config":{"name":"time-slicing-config","default":"any"}}}}'
kubectl label node "$NODE" nvidia.com/device-plugin.config=any --overwrite
kubectl -n "$GPU_NS" rollout restart daemonset/nvidia-device-plugin-daemonset
kubectl -n "$GPU_NS" rollout status daemonset/nvidia-device-plugin-daemonset --timeout=5m

shared="$(kubectl get node "$NODE" -o jsonpath='{.status.allocatable.nvidia\.com/gpu\.shared}')"
if [[ "$shared" != "3" ]]; then
  echo "expected $NODE to advertise 3 nvidia.com/gpu.shared shares; got ${shared:-0}" >&2
  exit 1
fi

NAMESPACE="$MODEL_NS" helmfile \
  -f deploy/models/nemotron35-lightning-llm-d/helmfile.yaml.gotmpl apply
kubectl -n "$MODEL_NS" get inferencepool,pods -o wide

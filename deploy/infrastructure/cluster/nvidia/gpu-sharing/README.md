# NVIDIA GPU Time-Slicing (TokenLabs cluster setup)

This directory contains cluster-level configuration for NVIDIA GPU Operator device-plugin time-slicing.
This is NOT app-level (helmfile) config — apply it separately.

## Prereqs
- NVIDIA GPU Operator installed (namespace typically `gpu-operator`).
- DGX Spark nodes present (default: `spark-01 spark-02`).

## Modes
### 1) MODE=shared (recommended)
- Node allocatable becomes:
  - `nvidia.com/gpu: 0`
  - `nvidia.com/gpu.shared: <replicas>`
- Workloads must request `nvidia.com/gpu.shared: 1`.

### 2) MODE=gpu (compatibility)
- Node allocatable becomes:
  - `nvidia.com/gpu: <replicas>`
  - `nvidia.com/gpu.shared: 0`
- Workloads continue to request `nvidia.com/gpu: 1` (but it now means a “slice”, not exclusive).

## Enable
Examples:

Enable shared resource name (gpu.shared), 4 replicas:
```bash
MODE=shared GPU_NS=gpu-operator NODES="spark-01 spark-02" ./enable-time-slicing.sh
```

Enable compatibility mode (keep nvidia.com/gpu), 4 replicas:
```bash
MODE=gpu GPU_NS=gpu-operator NODES="spark-01 spark-02" ./enable-time-slicing.sh
```

## Verify
```bash
kubectl describe node spark-01 | sed -n '/Allocatable:/,/System Info:/p'
```
Or:
```bash
kubectl get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\t"}}{{index .status.allocatable "nvidia.com/gpu"}}{{"\t"}}{{with index .status.allocatable "nvidia.com/gpu.shared"}}{{.}}{{else}}0{{end}}{{"\n"}}{{end}}'
```

## Disable/rollback
```bash
GPU_NS=gpu-operator NODES="spark-01 spark-02" ./disable-time-slicing.sh
```

## Notes for TokenLabs (llm-d-modelservice)

If MODE=shared, update modelservice requests/limits to nvidia.com/gpu.shared: "1".

If MODE=gpu, keep nvidia.com/gpu: "1" as-is.


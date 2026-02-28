# NVIDIA DRA Driver for GPUs

Attempted setup of Dynamic Resource Allocation (DRA) driver on the DGX Spark cluster.

## Status: Blocked

The DRA driver v25.12.0 does not support the NVIDIA GB10 GPU (DGX Spark). The kubelet plugin crashes when calling `nvmlDeviceGetMemoryInfo()` because GB10 uses unified memory, which NVML reports as "Not Supported".

```
Error: error creating driver: error enumerating all possible devices:
  error getting info for GPU 0: error getting memory info for device 0: Not Supported
```

The cluster uses the classic device plugin (`devicePlugin.enabled=true`) until NVIDIA adds GB10 support.

## Prerequisites

- Kubernetes v1.34.2+
- GPU Operator v25.10.0+
- NVIDIA Driver 580+

## Setup Steps

### 1. Upgrade MicroK8s to 1.34

Upgrade controller first, then workers:

```bash
# Controller
ssh nvidia@192.168.1.75 'sudo snap refresh microk8s --channel=1.34/stable'

# Workers
ssh nvidia@192.168.1.76 'sudo snap refresh microk8s --channel=1.34/stable'
ssh nvidia@192.168.1.77 'sudo snap refresh microk8s --channel=1.34/stable'
```

### 2. Label GPU nodes

```bash
microk8s kubectl label node spark-01 nvidia.com/dra-kubelet-plugin=true
microk8s kubectl label node spark-02 nvidia.com/dra-kubelet-plugin=true
```

The DRA kubelet plugin DaemonSet uses this label as a nodeSelector to target only GPU workers.

### 3. Label controller node

MicroK8s does not set the control-plane role label by default. The DRA controller pod requires it:

```bash
microk8s kubectl label node controller node-role.kubernetes.io/control-plane=""
```

### 4. Update GPU Operator (disable device plugin)

```bash
microk8s helm3 upgrade gpu-operator nvidia/gpu-operator \
  --version=v25.10.1 \
  --namespace gpu-operator \
  --set driver.enabled=false \
  --set devicePlugin.enabled=false \
  --set "driver.manager.env[0].name=NODE_LABEL_FOR_GPU_POD_EVICTION" \
  --set "driver.manager.env[0].value=nvidia.com/dra-kubelet-plugin"
```

- `driver.enabled=false` -- DGX Spark has pre-installed host drivers
- `devicePlugin.enabled=false` -- DRA replaces the classic device plugin
- `driver.manager.env` -- tells driver manager to use DRA label for pod eviction

### 5. Install DRA driver

```bash
microk8s helm3 upgrade -i nvidia-dra-driver-gpu nvidia/nvidia-dra-driver-gpu \
  --version="25.12.0" \
  --namespace nvidia-dra-driver-gpu \
  --create-namespace \
  --set gpuResourcesEnabledOverride=true \
  -f values.yaml
```

For host-installed drivers (DGX Spark), do NOT set `nvidiaDriverRoot=/run/nvidia/driver`. Leave it as the default (`/`) so the plugin finds binaries at their standard host paths (`/usr/bin/nvidia-smi`, `/usr/lib/`).

### values.yaml

```yaml
image:
  pullPolicy: IfNotPresent
kubeletPlugin:
  nodeSelector:
    nvidia.com/dra-kubelet-plugin: "true"
```

### 6. Validate

```bash
microk8s kubectl get pods -n nvidia-dra-driver-gpu -o wide
microk8s kubectl get deviceclass
microk8s kubectl get resourceslice
```

## Rollback

If DRA fails, re-enable the classic device plugin:

```bash
# Uninstall DRA driver
microk8s helm3 uninstall nvidia-dra-driver-gpu --namespace nvidia-dra-driver-gpu

# Re-enable device plugin
microk8s helm3 upgrade gpu-operator nvidia/gpu-operator \
  --version=v25.10.1 \
  --namespace gpu-operator \
  --set driver.enabled=false \
  --set devicePlugin.enabled=true
```

## Containerd Template Warning

The NVIDIA Container Toolkit may rewrite the MicroK8s containerd template to add `imports = ["/etc/containerd/conf.d/*.toml"]`. This imports the host containerd config (`99-nvidia.toml`), which breaks the CRI plugin by clobbering the `runc` runtime definition.

Symptoms: kubelet crash-loops with `unknown service runtime.v1.RuntimeService`.

Fix: Replace the containerd template with the standard MicroK8s one (match the controller). Do NOT use the `imports` directive. The `nvidia` runtime alias for GPU Operator compatibility should be added directly in the template:

```toml
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.nvidia]
  runtime_type = "io.containerd.runc.v2"
  [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.nvidia.options]
    BinaryName = "/usr/bin/nvidia-container-runtime"
```

## References

- [NVIDIA DRA Driver docs](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-intro-install.html)
- [k8s-dra-driver GitHub](https://github.com/NVIDIA/k8s-dra-driver)

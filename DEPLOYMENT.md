# Token Labs - Automated Cluster Setup

This repository provides end-to-end automation for standing up a DGX Spark cluster with Kubernetes, GPU support, and AI inference infrastructure.

## Quick Start

### 1. Bootstrap Nodes with Ansible

```bash
cd ansible
ansible-playbook -i inventory/hosts.yml site.yml
```

This will:
- Configure OS prerequisites (kernel modules, swap, sysctl)
- Install container runtime (containerd)
- Install Kubernetes (kubeadm, kubelet, kubectl)
- Install NVIDIA Container Toolkit
- Initialize Kubernetes cluster
- Join worker nodes

See [ansible/README.md](ansible/README.md) for details.

### 2. Deploy Infrastructure with Flux

```bash
# Install Flux CLI
curl -s https://fluxcd.io/install.sh | sudo bash

# Bootstrap Flux
export GITHUB_TOKEN=<your-token>
flux bootstrap github \
  --owner=elizabetht \
  --repository=token-labs \
  --branch=main \
  --path=./deploy \
  --personal
```

This will automatically deploy:
- Gateway API CRDs
- Longhorn (distributed storage)
- NVIDIA GPU Operator
- Envoy Gateway
- Kuadrant (auth + rate limiting)
- kube-prometheus-stack (monitoring)

See [deploy/flux-system/README.md](deploy/flux-system/README.md) for details.

### 3. Run Smoke Tests

```bash
cd deploy/tests
./01-test-cluster-nodes.sh
./02-test-gpu-access.sh
./03-test-inference.sh
./04-test-token-quota.sh
```

See [deploy/tests/README.md](deploy/tests/README.md) for details.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Client Requests                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Envoy Gateway                            │
│           (L7 Proxy + AI Gateway Extension)                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     Kuadrant                                │
│         Authorino (Auth) + Limitador (Rate Limit)           │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  AIGatewayRoute                             │
│            (Model Selection + Routing)                      │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  InferencePool                              │
│          (llm-d EPP Endpoint Picker)                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  vLLM Workers                               │
│        (LLaMA, Nemotron, Qwen on GPU nodes)                 │
└─────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
token-labs/
├── ansible/                    # Phase 1: Node bootstrap
│   ├── inventory/
│   │   └── hosts.yml          # DGX Spark node inventory
│   ├── roles/
│   │   ├── bootstrap/         # OS setup + Kubernetes install
│   │   ├── nvidia/            # NVIDIA Container Toolkit
│   │   ├── kubeadm-init/      # Control plane initialization
│   │   └── kubeadm-join/      # Worker node joining
│   └── site.yml               # Main playbook
│
├── deploy/                    # Phase 2-3: GitOps with Flux
│   ├── flux-system/           # Flux bootstrap manifests
│   │   ├── gotk-sync.yaml
│   │   └── gotk-kustomization.yaml
│   │
│   ├── infrastructure/        # Operators managed by Flux
│   │   ├── sources/           # HelmRepository + GitRepository
│   │   │   ├── helm-repositories.yaml
│   │   │   └── git-repositories.yaml
│   │   └── controllers/       # HelmRelease per operator
│   │       ├── gateway-api-crds.yaml
│   │       ├── longhorn.yaml
│   │       ├── gpu-operator.yaml
│   │       ├── nvidia-dra-driver.yaml
│   │       ├── envoy-gateway.yaml
│   │       ├── kuadrant.yaml
│   │       └── kube-prometheus-stack.yaml
│   │
│   ├── cluster/               # Cluster configs
│   │   └── nvidia/
│   │       └── gpu-sharing/   # GPU time-slicing configs
│   │
│   ├── gateway/               # Gateway API resources
│   ├── llm-d/                 # llm-d + model services
│   ├── policies/              # Auth + rate limit policies
│   ├── tenants/               # Tenant API keys
│   │
│   ├── tests/                 # Phase 4: Smoke tests
│   │   ├── 01-test-cluster-nodes.sh
│   │   ├── 02-test-gpu-access.sh
│   │   ├── 03-test-inference.sh
│   │   └── 04-test-token-quota.sh
│   │
│   └── scripts/               # Legacy imperative scripts
│       ├── 01-install-crds.sh
│       ├── 02-install-envoy-gateway.sh
│       ├── 03-install-kuadrant.sh
│       ├── 04-deploy-llm-d.sh
│       └── 05-deploy-services.sh
│
└── README.md                  # This file
```

## Components

### Installed Operators (via Flux)

| Component | Purpose | Namespace |
|-----------|---------|-----------|
| **Longhorn** | Distributed block storage | `longhorn-system` |
| **NVIDIA GPU Operator** | GPU device plugin, DCGM metrics | `gpu-operator` |
| **NVIDIA DRA Driver** | Dynamic Resource Allocation | `nvidia-dra-driver` |
| **Envoy Gateway** | L7 proxy and ingress | `envoy-gateway-system` |
| **Envoy AI Gateway** | AI-native routing | (extension) |
| **Kuadrant** | Auth (Authorino) + Rate Limiting (Limitador) | `kuadrant-system` |
| **llm-d** | Inference-aware scheduling | `token-labs` |
| **kube-prometheus-stack** | Prometheus + Grafana + Alertmanager | `kube-prometheus-stack` |

### Key Features

1. **GPU Sharing**
   - Time-slicing support (4 replicas per GPU)
   - Dynamic Resource Allocation (DRA) support
   - MIG (Multi-Instance GPU) support

2. **Multi-Model Inference**
   - LLaMA 3.1-8B, Nemotron-VL-12B, Qwen3-14B
   - vLLM workers with KV cache optimization
   - Inference-aware scheduling via llm-d EPP

3. **Token-Based Rate Limiting**
   - Per-tenant API keys
   - Token quota enforcement
   - Prometheus metrics for quota burn

4. **Observability**
   - GPU utilization metrics (DCGM)
   - Request latency and throughput
   - Token usage and quota tracking
   - Pre-built Grafana dashboards

## Deployment Phases

### Phase 1: Ansible Node Bootstrap ✅

Automates OS-level setup and Kubernetes installation:
- ✅ Inventory file for DGX Spark nodes
- ✅ Bootstrap role (hostname, swap, kernel modules, containerd)
- ✅ NVIDIA role (Container Toolkit, runtime config)
- ✅ kubeadm-init role (control plane + CNI)
- ✅ kubeadm-join role (worker nodes)
- ✅ Idempotent playbooks

### Phase 2: Flux GitOps Bootstrap ✅

Replaces imperative scripts with declarative GitOps:
- ✅ Flux system manifests
- ✅ HelmRepository and GitRepository sources
- ✅ Kustomization for infrastructure

### Phase 3: Kubernetes Operators ✅

Flux-managed HelmReleases for all operators:
- ✅ Longhorn (storage)
- ✅ NVIDIA GPU Operator (device plugin, DCGM)
- ✅ NVIDIA DRA Driver (fine-grained GPU scheduling)
- ✅ Gateway API CRDs (standard + inference extension)
- ✅ Envoy Gateway + AI Gateway
- ✅ Kuadrant (auth + rate limiting)
- ✅ kube-prometheus-stack (monitoring)

### Phase 4: Smoke Tests ✅

Automated validation scripts:
- ✅ Post-bootstrap test (nodes ready)
- ✅ GPU test (nvidia-smi in pod)
- ✅ Inference test (chat completion via gateway)
- ✅ Token quota test (rate limit enforcement)

## Operational Tasks

### Update a Kubernetes package version

Edit `ansible/inventory/hosts.yml`:
```yaml
k8s_version: "1.32"
k8s_package_version: "1.32.0-1.1"
```

Re-run Ansible:
```bash
cd ansible
ansible-playbook -i inventory/hosts.yml site.yml
```

### Update an operator version

Edit the HelmRelease in `deploy/infrastructure/controllers/`:
```yaml
spec:
  chart:
    spec:
      version: "1.8.x"  # Update version
```

Commit and push. Flux will reconcile automatically.

Or force immediate reconciliation:
```bash
flux reconcile helmrelease longhorn -n flux-system
```

### Add a new model

1. Create model directory: `deploy/<model-name>/`
2. Add PVC, ModelService, InferencePool manifests
3. Update AIGatewayRoute to include new model
4. Commit and push

### Scale inference workers

Edit InferencePool replicas:
```yaml
spec:
  replicas: 3  # Increase replicas
```

Or use kubectl:
```bash
kubectl scale inferencepool llama-3-8b -n token-labs --replicas=3
```

## Troubleshooting

### Ansible fails on a node

```bash
# Check connectivity
ansible all -i ansible/inventory/hosts.yml -m ping

# Run with verbose output
ansible-playbook -i ansible/inventory/hosts.yml site.yml -vvv

# Run on specific node
ansible-playbook -i ansible/inventory/hosts.yml site.yml --limit spark-02
```

### Flux HelmRelease fails

```bash
# Check status
flux get helmreleases -A

# View events
kubectl describe helmrelease longhorn -n flux-system

# Check controller logs
kubectl logs -n flux-system deploy/helm-controller --tail=100
```

### GPU not accessible

```bash
# Check GPU operator pods
kubectl get pods -n gpu-operator

# Check device plugin logs
kubectl logs -n gpu-operator -l app=nvidia-device-plugin-daemonset

# Verify GPU from host
nvidia-smi
```

### Inference requests failing

```bash
# Check gateway
kubectl get gateway -n token-labs
kubectl get svc -n envoy-gateway-system

# Check model pods
kubectl get pods -n token-labs -l app=vllm

# Check gateway logs
kubectl logs -n envoy-gateway-system -l gateway.envoyproxy.io/owning-gateway-name=ai-gateway
```

## Contributing

1. Make changes in a feature branch
2. Test locally with Ansible check mode and Flux dry-run
3. Submit PR with description of changes

## References

- [Kubernetes kubeadm](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/)
- [Flux CD](https://fluxcd.io/flux/)
- [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/)
- [Gateway API](https://gateway-api.sigs.k8s.io/)
- [Envoy Gateway](https://gateway.envoyproxy.io/)
- [Kuadrant](https://docs.kuadrant.io/)
- [Longhorn](https://longhorn.io/docs/)

## License

See LICENSE file.

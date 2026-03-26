# Cluster Setup

End-to-end runbook for standing up the token-labs Kubernetes cluster on DGX Spark nodes,
from bare OS to a fully GitOps-managed inference platform.

---

## Prerequisites

On your **local machine** (the machine you run these commands from):

| Tool | Version | Install |
|------|---------|---------|
| `ansible` | ≥ 2.15 | `pip install ansible` |
| `ansible` community.general collection | any | `ansible-galaxy collection install community.general` |
| `flux` CLI | ≥ 2.3 | [fluxcd.io/flux/installation](https://fluxcd.io/flux/installation/) |
| `kubectl` | matches cluster | [kubernetes.io/docs/tasks/tools](https://kubernetes.io/docs/tasks/tools/) |
| `age` + `sops` | any | `apt install age sops` / `brew install age sops` |
| GitHub PAT | `repo` scope | [github.com/settings/tokens](https://github.com/settings/tokens) |

SSH access to `spark-01` and `spark-02` as the `nvidia` user (passwordless sudo required).

---

## Phase 1 — Bootstrap nodes with Ansible

### 1. Verify connectivity

```bash
ansible -i ansible/inventory/hosts.yml all -m ping
```

### 2. Run the full playbook

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/site.yml
```

This runs four roles in order:

| Role | What it does |
|------|-------------|
| `bootstrap` | Loads kernel modules, disables swap, installs containerd + kubeadm/kubelet/kubectl (pinned) |
| `nvidia` | Installs nvidia-container-toolkit, sets NVIDIA as the default containerd runtime |
| `kubeadm-init` | Initialises the control-plane on `spark-01`, installs Flannel CNI |
| `kubeadm-join` | Joins `spark-02` as a worker node |

The playbook is idempotent — safe to re-run.

### 3. Copy kubeconfig locally

```bash
scp nvidia@spark-01:~/.kube/config ~/.kube/config
kubectl get nodes   # both nodes should show Ready
```

---

## Phase 2 — Bootstrap Flux (GitOps)

### 1. Generate a cluster age key (once per cluster)

```bash
age-keygen -o age.key
# The public key is printed to stdout — copy it, you'll need it in step 3.
```

### 2. Store the age private key as a Kubernetes Secret

```bash
cat age.key | kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/dev/stdin \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 3. Add the public key to `.sops.yaml` (repo root)

Create `.sops.yaml` if it doesn't exist:

```yaml
creation_rules:
  - path_regex: deploy/tenants/.*\.yaml$
    age: age1<your-public-key>
```

### 4. Run flux bootstrap

```bash
export GITHUB_TOKEN=<your-pat>

flux bootstrap github \
  --owner=elizabetht \
  --repository=token-labs \
  --branch=main \
  --path=deploy/clusters/token-labs \
  --personal
```

Flux will:
1. Install the Flux controllers into `flux-system`.
2. Commit the generated manifests to `deploy/flux-system/`.
3. Begin reconciling every `Kustomization` in `deploy/clusters/token-labs/infrastructure.yaml`.

### 5. Watch reconciliation

```bash
flux get kustomizations --watch
flux get helmreleases -A --watch
```

Reconciliation order (enforced by `dependsOn`):

```
infrastructure-sources
  └─ infrastructure-controllers
       ├─ longhorn
       ├─ nvidia-gpu-operator
       ├─ nvidia-dra-driver  (suspended — see note below)
       ├─ envoy-gateway
       ├─ envoy-ai-gateway
       ├─ kuadrant
       └─ kube-prometheus-stack
           └─ gateway
               └─ policies
                   └─ tenants
```

> **NVIDIA DRA Driver** is `suspend: true` because the GB10 GPU in DGX Spark uses
> unified CPU+GPU memory and is not yet supported by the DRA driver. It will be
> unsuspended once driver support lands. The classic device plugin is used instead.

---

## Phase 3 — Apply cluster-level resources

These are handled automatically by Flux once the controllers are healthy, but you
can apply them manually for a faster first-time setup:

```bash
# Gateway API CRDs (required before Envoy Gateway HelmRelease reconciles)
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.1/standard-install.yaml
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.3.0/manifests.yaml

# GPU time-slicing (optional — enable if you need to share a GPU across workloads)
bash deploy/cluster/nvidia/gpu-sharing/enable-time-slicing.sh
```

---

## Phase 4 — Smoke tests

Once all HelmReleases are `Ready`, run the smoke test:

```bash
export GATEWAY_HOST=api.tokenlabs.run
export API_KEY=tlabs_free_demo_key_change_me

bash deploy/scripts/smoke-test.sh
```

Expected output:

```
=== 1. Node readiness ===
  [PASS] All nodes are Ready

=== 2. GPU access ===
  [PASS] GPU detected inside pod: NVIDIA GB10

=== 3. Inference endpoint ===
  [PASS] Inference returned HTTP 200

=== 4. Token quota enforcement ===
  [PASS] Token quota enforcement returned HTTP 429 as expected

=== Results ===
  Passed: 4
  Failed: 0
```

---

## Encrypting tenant secrets with SOPS

New tenant API keys should be encrypted before committing:

```bash
# Create a new tenant file from the template
cp deploy/tenants/tenant-template.yaml deploy/tenants/tenant-acme.yaml
# Edit the file, then encrypt it
sops --encrypt --in-place deploy/tenants/tenant-acme.yaml
git add deploy/tenants/tenant-acme.yaml
git commit -m "Add tenant: acme"
```

Flux automatically decrypts secrets using the `sops-age` key stored in the cluster.

---

## Useful commands

```bash
# Re-run Ansible for a single role
ansible-playbook -i ansible/inventory/hosts.yml ansible/site.yml --tags nvidia

# Force Flux to reconcile immediately
flux reconcile kustomization infrastructure-controllers --with-source

# Check HelmRelease failures
flux get helmreleases -A | grep -v True

# Suspend/resume a HelmRelease (e.g. during a maintenance window)
flux suspend helmrelease nvidia-gpu-operator -n flux-system
flux resume helmrelease nvidia-gpu-operator -n flux-system
```

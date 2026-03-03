# ARC — GitHub Actions Runner Scale Set

Configures [Actions Runner Controller (ARC)](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners-with-actions-runner-controller/quickstart-for-actions-runner-controller) self-hosted runners on the TokenLabs MicroK8s cluster and pins them to the CPU controller node so that GPU worker nodes remain exclusively for inference workloads.

See [docs/GHA-RUNNER-NODE-PINNING.md](../../docs/GHA-RUNNER-NODE-PINNING.md) for a full explanation of how node pinning works, the difference between `template` and `listenerTemplate`, and how to handle taints.

## Quick start

### 1. Install the ARC controller (once per cluster)

```bash
helm upgrade --install arc \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-controller \
  -n arc-systems --create-namespace
```

### 2. Create the GitHub credentials secret

Using a [GitHub App](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners-with-actions-runner-controller/authenticating-to-the-github-api) (recommended):

```bash
kubectl create secret generic arc-github-secret \
  -n arc-runners \
  --from-literal=githubAppId=<APP_ID> \
  --from-literal=githubAppInstallationId=<INSTALLATION_ID> \
  --from-literal=githubAppPrivateKey="$(cat private-key.pem)"
```

Or using a PAT:

```bash
kubectl create secret generic arc-github-secret \
  -n arc-runners \
  --from-literal=githubToken=<PAT>
```

### 3. Label the controller node

```bash
kubectl label node controller gha-runner=cpu-controller
```

### 4. Deploy the runner scale set

```bash
helm upgrade --install arc-runners \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
  -n arc-runners --create-namespace \
  -f deploy/arc/values-runner-scale-set.yaml
```

### 5. Use the runner in a workflow

```yaml
jobs:
  my-job:
    runs-on: arc-runners   # matches the Helm release name
```

## Files

| File | Purpose |
|------|---------|
| `values-runner-scale-set.yaml` | Helm values that pin runner + listener pods to the controller node |

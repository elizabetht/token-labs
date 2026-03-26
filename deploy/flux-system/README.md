# flux-system

This directory is populated by `flux bootstrap`. Run the following once per cluster
(requires the `flux` CLI and a GitHub personal access token with `repo` scope):

```bash
flux bootstrap github \
  --owner=elizabetht \
  --repository=token-labs \
  --branch=main \
  --path=deploy \
  --personal
```

Flux will:
1. Install the Flux controllers into the `flux-system` namespace.
2. Commit the generated manifests back to this directory.
3. Begin reconciling every `Kustomization` and `HelmRelease` under `deploy/`.

## Secret management

Secrets (NVIDIA API keys, tenant API keys, etc.) are encrypted with **SOPS + age**.

```bash
# Generate a cluster age key (once):
age-keygen -o age.key
# Store the public key in .sops.yaml at the repo root.
# Store the private key as a Kubernetes secret:
cat age.key | kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/dev/stdin
```

Encrypt a secret file:
```bash
sops --encrypt --age $(grep "public key" age.key | awk '{ print $NF }') \
  deploy/tenants/tenant-pro.yaml > deploy/tenants/tenant-pro.enc.yaml
```

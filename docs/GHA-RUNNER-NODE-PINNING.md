# Pinning ARC Runner Pods to a Specific Node

This guide explains how to schedule [Actions Runner Controller (ARC)](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners-with-actions-runner-controller/quickstart-for-actions-runner-controller) `gha-runner-scale-set` pods onto a specific Kubernetes node — in this case, the CPU controller node — so that GPU worker nodes remain available exclusively for inference workloads.

---

## Cluster layout

```
┌──────────────────────────────────────────────────────────────────────┐
│                         MicroK8s Cluster                             │
│                                                                      │
│  ┌────────────────┐   ┌────────────────┐   ┌────────────────┐       │
│  │  controller     │   │  spark-01      │   │  spark-02      │       │
│  │  (CPU, ARM64)   │   │  (GB10 GPU)    │   │  (GB10 GPU)    │       │
│  │                 │   │                │   │                │       │
│  │  • Envoy GW     │   │  • vLLM        │   │  • vLLM        │       │
│  │  • Kuadrant     │   │  • Magpie TTS  │   │    (NL 12B)    │       │
│  │  • llm-d EPPs   │   │                │   │                │       │
│  │  • ARC listener │   │                │   │                │       │
│  │  • ARC runners  │   │                │   │                │       │
│  └────────────────┘   └────────────────┘   └────────────────┘       │
└──────────────────────────────────────────────────────────────────────┘
```

GitHub Actions runner pods (both the listener and the runner itself) run on `controller` — the CPU-only node — so that every GB of GPU memory on `spark-01` and `spark-02` stays available for LLM inference.

---

## Key concepts

### Runner pod vs. listener pod

ARC creates **two distinct pod types** per `RunnerScaleSet`:

| Pod | Name pattern | Purpose |
|-----|-------------|---------|
| **Listener** | `<release>-listener-*` | Long-polls the GitHub API; creates/deletes runner pods on demand. One per scale set. |
| **Runner** | `<release>-*` | Executes the actual CI workflow steps. Scaled 0 → N based on job queue. |

Each pod type has its own scheduling fields in the Helm chart:

| Pod | Helm key |
|-----|---------|
| Runner pod | `template.spec.nodeSelector` / `template.spec.tolerations` / `template.spec.affinity` |
| Listener pod | `listenerTemplate.spec.nodeSelector` / `listenerTemplate.spec.tolerations` / `listenerTemplate.spec.affinity` |

> **Important:** Setting `template` does **not** affect the listener pod, and vice versa. You must configure both independently.

---

## Step 1 — Label the target node

```bash
kubectl label node controller gha-runner=cpu-controller
```

Verify:

```bash
kubectl get node controller --show-labels | grep gha-runner
```

---

## Step 2 — Handle taints (if present)

If the controller node carries a taint (e.g., the default MicroK8s control-plane taint), runner pods will be rejected unless they declare a matching toleration.

Check for taints:

```bash
kubectl describe node controller | grep -A5 Taints:
```

If the output shows something like:

```
Taints: node-role.kubernetes.io/control-plane:NoSchedule
```

add the toleration shown in the values file below. If the output is `<none>`, you can omit the `tolerations` blocks.

---

## Step 3 — Apply Helm values

The values file [`deploy/arc/values-runner-scale-set.yaml`](../deploy/arc/values-runner-scale-set.yaml) pins both pods to the controller node.

Key sections:

```yaml
# Runner pod — executes CI steps
template:
  spec:
    nodeSelector:
      gha-runner: cpu-controller          # must match the label applied in Step 1
    tolerations:
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule                # remove if the node is untainted

# Listener pod — polls GitHub API, manages runner lifecycle
listenerTemplate:
  spec:
    nodeSelector:
      gha-runner: cpu-controller
    tolerations:
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule
```

Install (or upgrade) the scale set:

```bash
helm upgrade --install arc-runners \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
  -n arc-runners --create-namespace \
  -f deploy/arc/values-runner-scale-set.yaml
```

---

## Step 4 — Verify placement

```bash
# Listener pod
kubectl get pod -n arc-runners -l app.kubernetes.io/component=listener \
  -o wide

# Runner pods (created on demand when a job is queued)
kubectl get pod -n arc-runners -o wide
```

All pods should show `NODE=controller`.

---

## Using `affinity` instead of `nodeSelector`

`nodeSelector` is a simple exact-match label filter. If you need more expressive scheduling rules (e.g., prefer-but-don't-require), use `affinity`:

```yaml
template:
  spec:
    affinity:
      nodeAffinity:
        requiredDuringSchedulingIgnoredDuringExecution:
          nodeSelectorTerms:
            - matchExpressions:
                - key: gha-runner
                  operator: In
                  values: [cpu-controller]
    tolerations:
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule
```

You can combine `nodeSelector` and `affinity` — both constraints must be satisfied simultaneously.

---

## Kubernetes container mode

ARC supports a `containerMode.type: kubernetes` mode where each workflow job step runs in its own pod (instead of inside the runner pod). When this mode is active:

- The **runner pod** is still scheduled according to `template.spec`.
- The **workflow job pods** are created dynamically by the runner via Kubernetes container hooks. Their node placement is **not** automatically inherited from the runner pod.

To control where workflow job pods land, set a pod template via the `ACTIONS_RUNNER_CONTAINER_HOOKS_TEMPLATE_PATH` environment variable on the runner, pointing at a YAML file that includes the required `nodeSelector` and `tolerations`.

For the TokenLabs use case (shell-based steps only, no containerized job services), the default dind-equivalent behaviour keeps everything inside the runner pod, so `template.spec` is sufficient.

---

## Reference

| Resource | Where to configure |
|----------|-------------------|
| Runner pod node placement | `template.spec.nodeSelector` / `affinity` / `tolerations` |
| Listener pod node placement | `listenerTemplate.spec.nodeSelector` / `affinity` / `tolerations` |
| Workflow job pods (k8s mode) | Container hook pod template via `ACTIONS_RUNNER_CONTAINER_HOOKS_TEMPLATE_PATH` |

- [ARC Helm chart values reference](https://github.com/actions/actions-runner-controller/blob/master/charts/gha-runner-scale-set/values.yaml)
- [ARC quickstart](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners-with-actions-runner-controller/quickstart-for-actions-runner-controller)
- [Kubernetes node affinity docs](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/)

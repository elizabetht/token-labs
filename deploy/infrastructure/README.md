# Infrastructure

This directory owns cluster-scoped prerequisites, CRDs, and controllers.

- `sources/`: Flux chart and Git sources.
- `controllers/`: operator/controller installations, including pinned Dynamo.
- `crds/`: explicitly managed CRD compatibility assets.
- `cluster/`: node-level configuration such as NVIDIA GPU sharing.

Model workloads and shared Gateway instances do not belong here.

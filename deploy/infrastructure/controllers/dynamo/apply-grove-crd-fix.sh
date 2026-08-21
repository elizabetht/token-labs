#!/usr/bin/env bash
set -euo pipefail

CRD=clustertopologybindings.grove.io

if ! kubectl get crd "$CRD" >/dev/null 2>&1; then
  echo "$CRD is not installed; install/upgrade Dynamo Platform first" >&2
  exit 1
fi

# Grove alpha.11 bundles `ct`, but the legacy ClusterTopology CRD already owns
# that short name. A unique short name is required for this CRD to establish.
kubectl patch crd "$CRD" --type=merge \
  -p='{"spec":{"names":{"shortNames":["ctb"]}}}'

kubectl wait --for=condition=Established "crd/$CRD" --timeout=60s
kubectl api-resources --api-group=grove.io | grep clustertopologybindings

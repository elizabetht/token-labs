#!/bin/bash
# GPU test: Verify GPU access in containers
# Usage: ./02-test-gpu-access.sh

set -e

echo "=== GPU Access Test ==="
echo

echo "Running nvidia-smi in a test pod..."
echo

# Run nvidia-smi in a pod
kubectl run gpu-test \
  --image=nvcr.io/nvidia/cuda:12.3.0-base-ubuntu22.04 \
  --rm -it --restart=Never \
  --limits=nvidia.com/gpu=1 \
  -- nvidia-smi

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo
    echo "✅ SUCCESS: GPU is accessible from containers"
else
    echo
    echo "❌ FAIL: GPU test failed with exit code $exit_code"
    exit $exit_code
fi

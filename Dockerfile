FROM nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04

# Install build essentials and runtime dependencies
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update && apt-get install -y \
    python3.12 python3.12-dev python3.12-venv python3-pip \
    git wget patch curl ca-certificates cmake build-essential ninja-build \
    gcc-aarch64-linux-gnu g++-aarch64-linux-gnu \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Create virtual env
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip (use explicit path to ensure venv pip is used)
RUN --mount=type=cache,target=/root/.cache/pip /opt/venv/bin/pip install --upgrade pip

# Set CUDA architecture list for compilation (Grace Hopper GB10 is compute capability 12.1)
ARG TORCH_CUDA_ARCH_LIST='7.0 7.5 8.0 8.9 9.0 10.0 12.0'
ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}

# Install PyTorch + CUDA
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install pre-release deps
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install xgrammar triton && \
    /opt/venv/bin/pip install -U --pre flashinfer-python --index-url https://flashinfer.ai/whl/nightly --no-deps && \
    /opt/venv/bin/pip install flashinfer-python && \
    /opt/venv/bin/pip install -U --pre flashinfer-cubin --index-url https://flashinfer.ai/whl/nightly && \
    /opt/venv/bin/pip install -U --pre flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu130 && \
    /opt/venv/bin/pip install torchao>=0.14.0

# Set essential environment variables
ENV TORCH_CUDA_ARCH_LIST="12.1"
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV CUDA_HOME=/usr/local/cuda
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
ENV TORCH_USE_CUDA_DSA=0

# Install vLLM from PyPI
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install vllm==0.12.0

# Install LMCache and KV connectors (adapted from vLLM's pattern for venv usage)
# Note: LMCache disabled for ARM64 due to CUDA_HOME environment propagation issues in pip subprocess
# vLLM functions fully without LMCache - it's an optional caching layer
ARG INSTALL_KV_CONNECTORS=false
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements/kv_connectors.txt,target=/tmp/kv_connectors.txt,ro \
    CUDA_MAJOR="${CUDA_VERSION%%.*}"; \
    CUDA_VERSION_DASH=$(echo $CUDA_VERSION | cut -d. -f1,2 | tr '.' '-'); \
    BUILD_PKGS="libcusparse-dev-${CUDA_VERSION_DASH} \
                libcublas-dev-${CUDA_VERSION_DASH} \
                libcusolver-dev-${CUDA_VERSION_DASH}"; \
    if [ "$INSTALL_KV_CONNECTORS" = "true" ]; then \
        if [ "$CUDA_MAJOR" -ge 13 ]; then \
            /opt/venv/bin/pip install nixl-cu13; \
        fi; \
        /opt/venv/bin/pip install -r /tmp/kv_connectors.txt --no-build || ( \
            apt-get update -y && \
            apt-get install -y --no-install-recommends ${BUILD_PKGS} && \
            env CUDA_HOME=/usr/local/cuda PATH="/usr/local/cuda/bin:$PATH" LD_LIBRARY_PATH="/usr/local/cuda/lib64:$LD_LIBRARY_PATH" \
                /opt/venv/bin/pip install -r /tmp/kv_connectors.txt --no-build-isolation && \
            apt-get purge -y ${BUILD_PKGS} && \
            rm -rf /var/lib/apt/lists/* \
        ); \
    fi

# Clean up build artifacts
RUN rm -rf /tmp/*

# Set environment
ENV PATH="/opt/venv/bin:$PATH"
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Working directory
WORKDIR /app

# Expose port
EXPOSE 8000

ENTRYPOINT ["vllm", "serve"]

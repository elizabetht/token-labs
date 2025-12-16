# Token Labs vLLM Dockerfile
# 
# This Dockerfile builds vLLM for ARM64/CUDA 12.0 (Grace Hopper)
# 
# Version tags (v0.1.0, v0.2.0, v0.3.0) represent different feature configurations.
# See docs/DOCKERFILE_VERSIONS.md for version-specific features and configurations.
# 
# Benchmarking and deployment automation: https://github.com/elizabetht/token-labs-performance

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

# Install PyTorch + CUDA
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install pre-release deps
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install xgrammar triton && \
    /opt/venv/bin/pip install -U --pre flashinfer-python --index-url https://flashinfer.ai/whl/nightly --no-deps && \
    /opt/venv/bin/pip install flashinfer-python && \
    /opt/venv/bin/pip install -U --pre flashinfer-cubin --index-url https://flashinfer.ai/whl/nightly && \
    /opt/venv/bin/pip install -U --pre flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu130

# Set essential environment variables
ENV TORCH_CUDA_ARCH_LIST="12.1a"
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV CUDA_HOME=/usr/local/cuda
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
ENV TORCH_USE_CUDA_DSA=0

# Install vLLM from PyPI
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install vllm==0.12.0

# Clone and install LMCache
RUN --mount=type=cache,target=/root/.cache/git git clone https://github.com/LMCache/LMCache.git
WORKDIR /app/LMCache
RUN --mount=type=cache,target=/root/.cache/pip /opt/venv/bin/pip install -r requirements/build.txt

# Set additional environment variables specifically for LMCache build
ENV NVCC_APPEND_FLAGS="-gencode arch=compute_121,code=sm_121"

# Try installation without build isolation first, if it fails try with build isolation
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/app/LMCache/build \
    /opt/venv/bin/pip install -e . --no-build-isolation || pip install -e .

# Clean up build artifacts
RUN rm -rf /tmp/*

RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update && apt-get install -y python3-dev && rm -rf /var/lib/apt/lists/*

# Set environment
ENV PATH="/opt/venv/bin:$PATH"
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Working directory
WORKDIR /app

# Expose port
EXPOSE 8000

ENTRYPOINT ["vllm", "serve"]

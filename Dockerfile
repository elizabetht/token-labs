# syntax=docker/dockerfile:1.4
# Enable BuildKit for advanced caching features

# ============================================================================
# Build Stage: Compile vLLM and dependencies
# ============================================================================
FROM nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04 AS builder

# Set essential environment variables for build
# These are needed during compilation and should be set early
# CUDA_ARCH can be overridden for different GPU architectures
# Default 12.0f is used for vLLM on Grace Hopper (H100 GPU)
# Note: The 'f' suffix is vLLM-specific notation (not standard CUDA compute capability)
ARG CUDA_ARCH=12.0f
ENV TORCH_CUDA_ARCH_LIST=${CUDA_ARCH}
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV CUDA_HOME=/usr/local/cuda
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# Install build essentials and runtime dependencies
# Using cache mount for apt to speed up repeated builds
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y \
    python3.12 python3.12-dev python3.12-venv python3-pip \
    git wget patch curl ca-certificates cmake build-essential ninja-build

# Set working directory
WORKDIR /app

# Create virtual env
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip (use explicit path to ensure venv pip is used)
# Using cache mount for pip to reuse downloaded packages
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install --upgrade pip

# Install PyTorch + CUDA
# This is a large dependency and rarely changes, so it's cached separately
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install pre-release deps
# These are smaller and can be cached together
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install xgrammar triton

# Install flashinfer for ARM64/CUDA 13.0
# Separate RUN commands for better debugging and cache granularity
# First install without deps to avoid conflicts, then install with deps
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -U --pre flashinfer-python --index-url https://flashinfer.ai/whl/nightly --no-deps

# Install with dependencies to ensure all requirements are met
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install flashinfer-python

# Install additional flashinfer components
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -U --pre flashinfer-cubin --index-url https://flashinfer.ai/whl/nightly

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -U --pre flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu130

# Clone vLLM at a specific commit for cache stability
# Using a recent stable commit - update this SHA when you want to rebuild
ARG VLLM_COMMIT=main
RUN --mount=type=cache,target=/root/.cache/git \
    git clone https://github.com/vllm-project/vllm.git && \
    cd vllm && \
    git checkout ${VLLM_COMMIT}

WORKDIR /app/vllm

# Set optimized build environment variables for vLLM compilation
# Compile only for H100 (compute capability 9.0) to speed up build
# Allow auto-detection of CPU cores for parallel compilation
ENV TORCH_CUDA_ARCH_LIST=9.0
ENV FORCE_CUDA=1
ENV NVCC_THREADS=8

# Install build requirements for vLLM
# Cache pip downloads to speed up repeated builds
RUN python3 use_existing_torch.py
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements/build.txt

# Install vLLM with local build (source build for ARM64)
# This is the most time-consuming step; cache mount helps with partial rebuilds
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/app/vllm/build \
    pip install --no-build-isolation -e . -v --pre

# Clone and install LMCache
# Pin to a specific commit for reproducibility and caching
ARG LMCACHE_COMMIT=main
RUN --mount=type=cache,target=/root/.cache/git \
    git clone https://github.com/LMCache/LMCache.git && \
    cd /app/LMCache && \
    git checkout ${LMCACHE_COMMIT}

WORKDIR /app/LMCache
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements/build.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/app/LMCache/build \
    pip install -e . --no-build-isolation

# ============================================================================
# Runtime Stage: Minimal image with only necessary components
# ============================================================================
FROM nvidia/cuda:13.0.2-cudnn-runtime-ubuntu24.04 AS runtime

# Install only runtime dependencies (no build tools needed)
# python3-dev is included for potential JIT compilation needs
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y \
    python3.12 python3.12-dev python3.12-venv

# Copy the virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy vLLM installation
COPY --from=builder /app/vllm /app/vllm

# Copy LMCache installation
COPY --from=builder /app/LMCache /app/LMCache

# Set environment variables
# Use same CUDA_ARCH from builder stage
ARG CUDA_ARCH=12.0f
ENV PATH="/opt/venv/bin:$PATH"
ENV TORCH_CUDA_ARCH_LIST=${CUDA_ARCH}
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV CUDA_HOME=/usr/local/cuda
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Working directory
WORKDIR /app

# Expose port
EXPOSE 8000

# Default entrypoint
ENTRYPOINT ["vllm", "serve"]

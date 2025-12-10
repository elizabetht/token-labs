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
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install pre-release deps
RUN --mount=type=cache,target=/root/.cache/pip pip install xgrammar triton && \
    --mount=type=cache,target=/root/.cache/pip pip install -U --pre flashinfer-python --index-url https://flashinfer.ai/whl/nightly --no-deps && \
    --mount=type=cache,target=/root/.cache/pip pip install flashinfer-python && \
    --mount=type=cache,target=/root/.cache/pip pip install -U --pre flashinfer-cubin --index-url https://flashinfer.ai/whl/nightly && \
    --mount=type=cache,target=/root/.cache/pip pip install -U --pre flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu130

# Set essential environment variables for build BEFORE building packages
ENV TORCH_CUDA_ARCH_LIST="12.0f"
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV CUDA_HOME=/usr/local/cuda
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
ENV FORCE_CUDA=1
ENV MAX_JOBS=8
ENV TORCH_USE_CUDA_DSA=0

# Clone vLLM
RUN --mount=type=cache,target=/root/.cache/git \
    git clone https://github.com/vllm-project/vllm.git

WORKDIR /app/vllm

# Install build requirements for vLLM
RUN python3 use_existing_torch.py
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements/build.txt

# Install vLLM with local build (source build for ARM64)
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/app/vllm/build \
    pip install --no-build-isolation -e . -v --pre

RUN --mount=type=cache,target=/root/.cache/git git clone https://github.com/LMCache/LMCache.git
WORKDIR /app/vllm/LMCache
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements/build.txt

# Set additional environment variables specifically for LMCache build
ENV NVCC_APPEND_FLAGS="-gencode arch=compute_121,code=sm_121"

# Try installation without build isolation first, if it fails try with build isolation
RUN --mount=type=cache,target=/root/.cache/pip pip install -e . --no-build-isolation || pip install -e .

# Clean up build artifacts
RUN rm -rf /app/vllm/.git && rm -rf /root/.cache/pip && rm -rf /tmp/* && rm -rf /app/LMCache/.git

RUN apt install -y python3-dev

# Set environment
ENV PATH="/opt/venv/bin:$PATH"
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Working directory
WORKDIR /app

# Expose port
EXPOSE 8000

ENTRYPOINT ["vllm", "serve"]

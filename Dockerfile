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
    /opt/venv/bin/pip install -U --pre flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu130 && \
    /opt/venv/bin/pip install torchao>=0.14.0

# Set essential environment variables
ENV TORCH_CUDA_ARCH_LIST="12.1a"
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV CUDA_HOME=/usr/local/cuda
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
ENV TORCH_USE_CUDA_DSA=0

# Install vLLM from PyPI
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install vllm==0.13.0

# Clean up build artifacts
RUN rm -rf /tmp/*

# Set environment
ENV PATH="/opt/venv/bin:$PATH"
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

EXPOSE 8000

ENTRYPOINT ["vllm", "serve"]

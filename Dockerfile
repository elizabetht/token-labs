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

# Install PyTorch + CUDA (no cache to ensure we get cu130 version, not cpu)
RUN /opt/venv/bin/pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

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

# Install vLLM from PyPI (this will install CPU torch)
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install vllm --extra-index-url https://wheels.vllm.ai/0.13.0/cu130 --extra-index-url https://download.pytorch.org/whl/cu130

# Install Ray explicitly — the cu130 vLLM wheel omits it, but PP=2 over Ray requires it
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install "ray[default]>=2.9,!=2.10.0"

# Pre-cache HarmonyGptOss vocab (identical to o200k_base — confirmed same SHA256)
RUN mkdir -p /opt/tiktoken-cache && \
    wget -q "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken" \
         -O /opt/tiktoken-cache/HarmonyGptOss.tiktoken && \
    cp /opt/tiktoken-cache/HarmonyGptOss.tiktoken \
       "/opt/tiktoken-cache/446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d" && \
    echo "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d  /opt/tiktoken-cache/HarmonyGptOss.tiktoken" | sha256sum -c

ENV TIKTOKEN_RS_CACHE_DIR=/opt/tiktoken-cache
ENV TIKTOKEN_ENCODINGS_BASE=http://127.0.0.1:18889

# Force reinstall PyTorch with CUDA after vLLM (vLLM pulls in CPU torch)
RUN /opt/venv/bin/pip install --no-cache-dir --force-reinstall --no-deps torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Clean up build artifacts
RUN rm -rf /tmp/*

# Set environment
ENV PATH="/opt/venv/bin:$PATH"
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

EXPOSE 8000

ENTRYPOINT ["vllm", "serve"]

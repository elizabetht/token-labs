FROM nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04

# Install Python and build dependencies needed for CUDA extensions
RUN --mount=type=cache,target=/var/cache/apt \
    apt-get update && apt-get install -y \
    python3.12 python3.12-dev python3.12-venv python3-pip \
    build-essential cmake ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Create virtual env
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV CUDA_HOME=/usr/local/cuda

# Upgrade pip and install Python build tools
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install --upgrade pip setuptools==79.0.1 setuptools_scm packaging wheel

# Install numpy first (required for LMCache CUDA extension build)
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install numpy==1.26.4

# Install PyTorch + CUDA
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install flashinfer dependencies
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install xgrammar triton && \
    /opt/venv/bin/pip install -U --pre flashinfer-python --index-url https://flashinfer.ai/whl/nightly --no-deps && \
    /opt/venv/bin/pip install flashinfer-python && \
    /opt/venv/bin/pip install -U --pre flashinfer-cubin --index-url https://flashinfer.ai/whl/nightly && \
    /opt/venv/bin/pip install -U --pre flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu130

# Set essential environment variables
ENV TORCH_CUDA_ARCH_LIST="12.1a"
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
ENV TORCH_USE_CUDA_DSA=0

# Install vLLM from PyPI
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install vllm==0.12.0

# Install LMCache from PyPI with --no-build-isolation to use pre-installed numpy
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install --no-build-isolation lmcache==0.3.9

# Set runtime environment
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Set working directory
WORKDIR /app

# Expose port
EXPOSE 8000

ENTRYPOINT ["vllm", "serve"]

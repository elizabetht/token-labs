# Dockerfile for vLLM on DGX Spark (Grace Hopper)
# Multi-stage build to reduce image size

# ============================================
# STAGE 1: BUILD
# ============================================
FROM nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04 AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3.12 \
    python3.12-dev \
    python3.12-venv \
    python3-pip \
    cmake \
    ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Create virtual env
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip
RUN pip install --upgrade pip

# Set environment for DGX Spark (CUDA arch 12.1f)
ENV TORCH_CUDA_ARCH_LIST=12.1f
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV PATH=/usr/local/cuda/bin:$PATH
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# Install PyTorch + CUDA
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install vLLM dependencies
RUN pip install --no-cache-dir \
    xgrammar \
    triton

# Try to install flashinfer (may have prebuilt wheels for ARM64)
RUN pip install --no-cache-dir flashinfer-python --prerelease=allow || echo "flashinfer not available, skipping"

# Clone vLLM
ARG VLLM_VERSION=main
RUN git clone --depth 1 --branch ${VLLM_VERSION} https://github.com/vllm-project/vllm.git /vllm

# Build vLLM from source
WORKDIR /vllm
RUN python3 use_existing_torch.py 
RUN pip install --no-cache-dir -r requirements/build.txt 
RUN VLLM_USE_PRECOMPILED=1 pip install --no-build-isolation --editable . --pre

# Clean up build artifacts to reduce size
RUN rm -rf /vllm/.git \
    && rm -rf /root/.cache/pip \
    && rm -rf /tmp/* \
    && find /opt/venv -name "*.pyc" -delete \
    && find /opt/venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# ============================================
# STAGE 2: RUNTIME
# ============================================
# Using devel image because vLLM requires full CUDA libraries (libcudart, etc.)
FROM nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04 AS runtime

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy vLLM source (needed for editable install)
COPY --from=builder /vllm /vllm

# Set up environment
ENV PATH="/opt/venv/bin:/usr/local/cuda/bin:$PATH"
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:/usr/local/cuda/compat:$LD_LIBRARY_PATH
ENV CUDA_HOME=/usr/local/cuda
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

EXPOSE 8000

# Usage: docker run image:tag <model-name> [vllm-options]
# Example: docker run image:tag meta-llama/Llama-3.1-8B-Instruct --gpu-memory-utilization 0.7
ENTRYPOINT ["vllm", "serve"]

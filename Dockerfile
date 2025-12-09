# ============================================
# Stage 1: Builder - compile vLLM from source
# ============================================
FROM nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04 AS builder

# Install build essentials
RUN apt-get update && apt-get install -y \
    python3.12 python3.12-dev python3.12-venv python3-pip \
    git wget patch curl ca-certificates cmake build-essential ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Create virtual env
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip (use explicit path to ensure venv pip is used)
RUN /opt/venv/bin/pip install --upgrade pip

# Install PyTorch + CUDA
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install pre-release deps
RUN pip install xgrammar triton

# Install flashinfer for ARM64/CUDA 13.0
RUN pip install -U --pre flashinfer-python --index-url https://flashinfer.ai/whl/nightly --no-deps
RUN pip install flashinfer-python
RUN pip install -U --pre flashinfer-cubin --index-url https://flashinfer.ai/whl/nightly
RUN pip install -U --pre flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu130

# Clone vLLM
RUN git clone https://github.com/vllm-project/vllm.git

WORKDIR /app/vllm

RUN python3 use_existing_torch.py
RUN pip install -r requirements/build.txt

# Set essential environment variables for build
ENV TORCH_CUDA_ARCH_LIST=12.0f
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV CUDA_HOME=/usr/local/cuda

# Install vLLM with local build (source build for ARM64)
RUN pip install --no-build-isolation -e . -v --pre

# Clean up build artifacts
RUN rm -rf /app/vllm/.git && rm -rf /root/.cache/pip && rm -rf /tmp/*

# ============================================
# Stage 2: Runtime - minimal production image
# ============================================
FROM nvidia/cuda:13.0.2-cudnn-runtime-ubuntu24.04 AS runtime

# Install minimal runtime dependencies
RUN apt-get update && apt-get install -y \
    python3.12 python3.12-venv build-essential\
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy vLLM source (needed for editable install)
COPY --from=builder /app/vllm /app/vllm

# Set environment
ENV PATH="/opt/venv/bin:$PATH"
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Working directory
WORKDIR /app

# Expose port
EXPOSE 8000

ENTRYPOINT ["vllm", "serve"]

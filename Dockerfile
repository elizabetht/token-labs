# Dockerfile for vLLM on DGX Spark (Grace Hopper)
# Single-stage build to ensure CUDA libraries are available at runtime

FROM nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04

# Install essentials
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-dev \
    python3.12-venv \
    python3-pip \
    git \
    wget \
    cmake \
    build-essential \
    ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Create virtual env
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip
RUN pip install --upgrade pip

# Set environment for DGX Spark (CUDA arch 12.0f for Blackwell)
ENV TORCH_CUDA_ARCH_LIST=12.0f
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV PATH=/usr/local/cuda/bin:$PATH
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# Install PyTorch + CUDA
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install pre-release deps
RUN pip install xgrammar triton flashinfer-python --pre

# Clone vLLM
ARG VLLM_VERSION=main
RUN git clone --depth 1 --branch ${VLLM_VERSION} https://github.com/vllm-project/vllm.git /app/vllm

# Build vLLM from source
WORKDIR /app/vllm
RUN python3 use_existing_torch.py
RUN pip install -r requirements/build.txt
RUN pip install --no-build-isolation -e . -v --pre

# Clean up to reduce image size
RUN rm -rf /app/vllm/.git \
    && rm -rf /root/.cache/pip \
    && rm -rf /tmp/*

# Set runtime environment
ENV CUDA_HOME=/usr/local/cuda
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

EXPOSE 8000

# Usage: docker run --gpus all image:tag meta-llama/Llama-3.1-8B-Instruct [vllm-options]
ENTRYPOINT ["vllm", "serve"]

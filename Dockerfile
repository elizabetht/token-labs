FROM nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04

# Install essentials
RUN apt-get update && apt-get install -y \
    python3.12 python3.12-dev python3.12-venv python3-pip \
    git wget cmake curl ca-certificates build-essential ninja-build \
    && rm -rf /var/lib/apt/lists/* \
    && curl --version

WORKDIR /app

# Create venv
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# CUDA env
ENV CUDA_HOME=/usr/local/cuda
ENV PATH=/usr/local/cuda/bin:/usr/bin:/bin:${PATH}
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}
ENV TORCH_CUDA_ARCH_LIST=12.0
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas

# Install PyTorch + deps (same as host)
RUN pip install --upgrade pip
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
RUN pip install xgrammar triton flashinfer-python --pre

# Clone vLLM
ARG VLLM_VERSION=main
RUN git clone --depth 1 --branch ${VLLM_VERSION} https://github.com/vllm-project/vllm.git /app/vllm

WORKDIR /app/vllm

# Same build steps as host
RUN python3 use_existing_torch.py
RUN pip install -r requirements/build.txt

RUN rm -rf build dist vllm.egg-info
ENV VLLM_USE_PRECOMPILED=1
ENV VLLM_MAIN_CUDA_VERSION=13.0
RUN pip install --no-build-isolation -e . -v --pre

# Clean up (optional)
RUN rm -rf .git && rm -rf /root/.cache/pip && rm -rf /tmp/*

ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

EXPOSE 8000

ENTRYPOINT []

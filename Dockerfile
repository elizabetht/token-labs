FROM nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04

# Install essentials
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

# Merge PR #26844 for ARM64/Grace Hopper support
RUN git fetch origin pull/26844/head:pr-26844
RUN git -c user.name="CI Bot" -c user.email="ci@example.com" merge --no-ff --no-edit pr-26844

RUN python3 use_existing_torch.py
RUN sed -i "/flashinfer/d" requirements/cuda.txt
RUN pip install -r requirements/build.txt

# Set essential environment variables
ENV TORCH_CUDA_ARCH_LIST=12.1a
ENV TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
ENV TIKTOKEN_ENCODINGS_BASE=/app/tiktoken_encodings
ENV CUDA_HOME=/usr/local/cuda

# Install vLLM with local build (source build for ARM64)
RUN pip install --no-build-isolation -e . -v --pre

# Clean up
RUN rm -rf .git && rm -rf /root/.cache/pip && rm -rf /tmp/*

ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Expose port
EXPOSE 8000

ENTRYPOINT ["vllm", "serve"]

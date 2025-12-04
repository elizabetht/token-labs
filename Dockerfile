# Multi-arch Dockerfile for vLLM
# - linux/amd64: Uses pre-built vLLM image (CUDA x86_64)
# - linux/arm64: Builds vLLM for ARM (CUDA ARM64, e.g., DGX Spark, Jetson)

ARG TARGETARCH

#######################################
# ARM64 build (DGX Spark, Jetson, etc.)
#######################################
FROM nvcr.io/nvidia/pytorch:24.01-py3 AS base-arm64

# Install vLLM for ARM64
RUN pip install --no-cache-dir vllm

#######################################
# AMD64 build (traditional x86_64 GPUs)
#######################################
FROM vllm/vllm-openai:v0.11.2 AS base-amd64

#######################################
# Final stage - select based on arch
#######################################
FROM base-${TARGETARCH} AS final

ENV PORT=8000
ENV HOST=0.0.0.0

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
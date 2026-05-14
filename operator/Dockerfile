# syntax=docker/dockerfile:1
FROM golang:1.24 AS builder
WORKDIR /workspace

# Download dependencies first (cache layer)
COPY go.mod go.sum ./
RUN go mod download

# Build the manager binary
COPY api/ api/
COPY cmd/ cmd/
COPY internal/ internal/
RUN CGO_ENABLED=0 GOOS=linux go build -a -o manager ./cmd/

# Minimal runtime image
FROM gcr.io/distroless/static:nonroot
WORKDIR /
COPY --from=builder /workspace/manager .
USER 65532:65532
ENTRYPOINT ["/manager"]

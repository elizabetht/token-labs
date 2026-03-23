# Build stage
FROM golang:1.24-alpine AS builder

WORKDIR /workspace

COPY go.mod go.sum ./
RUN go mod download

COPY api/ api/
COPY cmd/ cmd/
COPY internal/ internal/

RUN CGO_ENABLED=0 GOOS=linux go build -a -o tenant-operator ./cmd/

# Runtime stage — distroless for minimal attack surface
FROM gcr.io/distroless/static:nonroot

WORKDIR /
COPY --from=builder /workspace/tenant-operator .
USER 65532:65532

ENTRYPOINT ["/tenant-operator"]

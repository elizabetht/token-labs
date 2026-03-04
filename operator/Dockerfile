# Build stage
FROM golang:1.23-alpine AS builder

WORKDIR /workspace
COPY go.mod go.sum ./
RUN go mod download

COPY api/ api/
COPY controllers/ controllers/
COPY main.go main.go

RUN CGO_ENABLED=0 GOOS=linux go build -a -o manager main.go

# Runtime stage
FROM gcr.io/distroless/static:nonroot

WORKDIR /
COPY --from=builder /workspace/manager .

USER 65532:65532
ENTRYPOINT ["/manager"]

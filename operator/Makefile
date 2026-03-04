# ModelDeployment Operator Makefile

IMAGE_REGISTRY ?= ghcr.io/elizabetht/token-labs
IMAGE_NAME      ?= modeldeployment-operator
IMAGE_TAG       ?= latest
IMG             ?= $(IMAGE_REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)

.PHONY: all build test lint docker-build docker-push install uninstall deploy undeploy

all: build

## Build the operator binary
build:
	go build -o bin/manager main.go

## Run unit tests
test:
	go test ./... -v

## Lint with golangci-lint (must be installed)
lint:
	golangci-lint run ./...

## Build Docker image
docker-build:
	docker build -t $(IMG) .

## Push Docker image
docker-push:
	docker push $(IMG)

## Install the CRD into the cluster
install:
	kubectl apply -f ../deploy/crds/tokenlabs.io_modeldeployments.yaml

## Uninstall the CRD from the cluster
uninstall:
	kubectl delete -f ../deploy/crds/tokenlabs.io_modeldeployments.yaml

## Deploy the operator into the cluster
deploy:
	kubectl apply -f ../deploy/operator/operator.yaml

## Undeploy the operator from the cluster
undeploy:
	kubectl delete -f ../deploy/operator/operator.yaml

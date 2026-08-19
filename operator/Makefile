IMG ?= ghcr.io/elizabetht/token-labs/inferencepool-controller:latest

.PHONY: all build test fmt vet generate manifests docker-build docker-push install uninstall deploy

all: build

## Build manager binary
build: fmt vet
	go build -o bin/manager ./cmd/

## Run tests
test: fmt vet
	go test ./... -v

## Run go fmt
fmt:
	go fmt ./...

## Run go vet
vet:
	go vet ./...

## Build docker image
docker-build:
	docker build -t ${IMG} .

## Push docker image
docker-push:
	docker push ${IMG}

## Install CRDs into the cluster
install:
	kubectl apply -f config/crd/bases/

## Uninstall CRDs from the cluster
uninstall:
	kubectl delete -f config/crd/bases/

## Deploy the controller to the cluster (requires IMG to be set)
deploy:
	kubectl apply -f config/crd/bases/
	kubectl set image deployment/inferencepool-controller manager=${IMG} -n token-labs || \
	  kubectl create deployment inferencepool-controller --image=${IMG} -n token-labs

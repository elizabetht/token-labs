{
  description = "Token Labs - Self-hosted LLM inference on NVIDIA DGX Spark";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config = {
            allowUnfree = true;
            cudaSupport = true;
          };
        };

        # Python environment with required dependencies
        pythonEnv = pkgs.python312.withPackages (ps: with ps; [
          pip
          virtualenv
          setuptools
          wheel
        ]);

      in
      {
        # Development shell
        devShells.default = pkgs.mkShell {
          name = "token-labs-dev";
          
          buildInputs = with pkgs; [
            # Python and build tools
            pythonEnv
            python312Packages.pip
            
            # Docker tools
            docker
            docker-compose
            
            # Build essentials
            gcc
            gnumake
            cmake
            ninja
            
            # Version control and utilities
            git
            curl
            wget
            
            # CUDA toolkit (for local development if NVIDIA GPU is available)
            # Note: This assumes CUDA support in nixpkgs
            # cudaPackages.cuda_cudart
            # cudaPackages.cudnn
          ];

          shellHook = ''
            echo "🚀 Token Labs Development Environment"
            echo "======================================"
            echo ""
            echo "Available commands:"
            echo "  docker build -t token-labs-vllm ."
            echo "  docker run --gpus all -p 8000:8000 token-labs-vllm"
            echo ""
            echo "Python version: $(python3 --version)"
            echo "Docker version: $(docker --version)"
            echo ""
            
            # Set up Python virtual environment if it doesn't exist
            if [ ! -d ".venv" ]; then
              echo "Creating Python virtual environment..."
              python3 -m venv .venv
            fi
            
            echo "To activate Python venv: source .venv/bin/activate"
          '';
        };

        # Package for building the Docker image using Nix
        packages = {
          # Docker image builder
          docker-image = pkgs.dockerTools.buildLayeredImage {
            name = "token-labs-vllm";
            tag = "latest";
            
            contents = with pkgs; [
              bashInteractive
              coreutils
              pythonEnv
            ];

            config = {
              Cmd = [ "/bin/bash" ];
              WorkingDir = "/app";
              ExposedPorts = {
                "8000/tcp" = {};
              };
              Env = [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                "NVIDIA_VISIBLE_DEVICES=all"
                "NVIDIA_DRIVER_CAPABILITIES=compute,utility"
              ];
            };

            extraCommands = ''
              mkdir -p app
            '';
          };

          default = self.packages.${system}.docker-image;
        };

        # CI/CD helper apps
        apps = {
          # Build Docker image using the Dockerfile
          build-docker = {
            type = "app";
            program = toString (pkgs.writeShellScript "build-docker" ''
              set -euo pipefail
              echo "Building Docker image for token-labs..."
              ${pkgs.docker}/bin/docker build -t token-labs-vllm:latest .
              echo "✅ Docker image built successfully!"
            '');
          };

          # Run the Docker container
          run-docker = {
            type = "app";
            program = toString (pkgs.writeShellScript "run-docker" ''
              set -euo pipefail
              MODEL_NAME=''${MODEL_NAME:-meta-llama/Llama-3.1-8B-Instruct}
              echo "Starting vLLM server with model: $MODEL_NAME"
              ${pkgs.docker}/bin/docker run --rm \
                --gpus all \
                -p 8000:8000 \
                -e MODEL_NAME="$MODEL_NAME" \
                token-labs-vllm:latest
            '');
          };

          default = self.apps.${system}.build-docker;
        };

        # Formatter for Nix files
        formatter = pkgs.nixpkgs-fmt;
      }
    );
}

# Nix Flake Documentation

This repository includes a Nix flake (`flake.nix`) that provides a reproducible development environment and build tools for Token Labs.

## What is a Nix Flake?

Nix flakes are a way to manage reproducible, composable software environments. They provide:
- **Reproducibility**: Exact versions of all dependencies are locked
- **Composability**: Easy sharing and reuse of configurations
- **Isolation**: No conflicts with system-installed packages

## Prerequisites

1. **Install Nix** (with flakes enabled):
   ```bash
   # Install Nix
   curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix | sh -s -- install
   
   # Or if you already have Nix, enable flakes
   mkdir -p ~/.config/nix
   echo "experimental-features = nix-command flakes" >> ~/.config/nix/nix.conf
   ```

2. **(Optional) Install direnv** for automatic environment loading:
   ```bash
   # On Ubuntu/Debian
   sudo apt install direnv
   
   # On macOS
   brew install direnv
   
   # Add to your shell rc file (~/.bashrc, ~/.zshrc, etc.)
   eval "$(direnv hook bash)"  # or zsh, fish, etc.
   ```

## Usage

### Development Environment

Enter the development shell with all dependencies:

```bash
nix develop
```

This provides:
- Python 3.12 with pip, virtualenv, setuptools
- Docker and Docker Compose
- Build tools (gcc, cmake, ninja)
- Git, curl, wget

With direnv installed, the environment activates automatically when you `cd` into the directory (after running `direnv allow`).

### Building Docker Image

Use the Nix app to build the Docker image:

```bash
nix run .#build-docker
```

This is equivalent to:
```bash
docker build -t token-labs-vllm:latest .
```

### Running Docker Container

Run the container with:

```bash
nix run .#run-docker
```

Or with a custom model:
```bash
MODEL_NAME=meta-llama/Llama-3.1-8B-Instruct nix run .#run-docker
```

### Available Commands

List all available apps and packages:
```bash
nix flake show
```

Format Nix files:
```bash
nix fmt
```

## Flake Structure

The `flake.nix` provides:

1. **Development Shell** (`devShells.default`):
   - Complete development environment
   - Auto-creates Python virtual environment
   - Includes Docker and build tools

2. **Packages** (`packages.docker-image`):
   - Nix-built Docker image (alternative to Dockerfile)
   - Layered for efficient caching

3. **Apps**:
   - `build-docker`: Build Docker image using Dockerfile
   - `run-docker`: Run the Docker container
   - `default`: Alias for `build-docker`

## Integration with Existing Workflow

The Nix flake complements the existing Docker workflow:

- **Docker builds** still use the `Dockerfile` for production
- **Nix flake** provides:
  - Reproducible dev environment
  - Helper scripts for common tasks
  - Alternative Docker image building (optional)

## CI/CD Integration

You can use Nix in GitHub Actions:

```yaml
- uses: DeterminateSystems/nix-installer-action@main
- uses: DeterminateSystems/magic-nix-cache-action@main
- run: nix develop --command bash -c "your-command-here"
```

## Troubleshooting

### Flake evaluation errors
```bash
# Update flake inputs
nix flake update

# Check flake validity
nix flake check
```

### Docker permissions
If you get permission errors with Docker:
```bash
sudo usermod -aG docker $USER
newgrp docker
```

### direnv not loading
```bash
# Allow direnv for this directory
direnv allow
```

## Further Reading

- [Nix Flakes Documentation](https://nixos.wiki/wiki/Flakes)
- [Zero to Nix](https://zero-to-nix.com/)
- [Nix.dev](https://nix.dev/)

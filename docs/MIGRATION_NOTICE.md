# Migration Notice: Deploy and Benchmark Scripts

## Overview

As of December 2025, the deployment and benchmarking scripts have been migrated from this repository to a dedicated repository for better separation of concerns and independent versioning.

## What Was Migrated

The following files and workflows have been moved to [token-labs-performance](https://github.com/elizabetht/token-labs-performance):

### Scripts
- `scripts/generate_results.py` - Generates benchmark results JSON
- `scripts/update_pricing.py` - Updates pricing in documentation

### Workflows
- `.github/workflows/deploy-and-benchmark.yml` - Deployment and benchmark automation

## What Remains in This Repository

This repository (`token-labs`) continues to maintain:

- **Dockerfile** - vLLM image definition for ARM64/CUDA 12.0
- **entrypoint.sh** - Docker container entrypoint script
- **config/** - Configuration files (LMCache settings)
- **docs/** - Benchmark results and documentation
  - Published to GitHub Pages at [www.tokenlabs.run](https://elizabetht.github.io/token-labs/)
  - Results automatically updated by token-labs-performance repo
- **.github/workflows/build-and-push.yml** - Docker image build workflow

## Why the Migration?

The migration provides several benefits:

1. **Separation of Concerns**
   - `token-labs`: Infrastructure and Docker image builds
   - `token-labs-performance`: Deployment, testing, and benchmarking

2. **Independent Versioning**
   - Dockerfile versions (v0.1.0, v0.2.0, v0.3.0) can be tested independently
   - Benchmark scripts can evolve without affecting image builds

3. **Explicit Version Testing**
   - Each feature enablement is tagged and tested against specific Dockerfile versions
   - Clear documentation of which version supports which features

4. **Cleaner Repository Structure**
   - Focused repositories with clear responsibilities
   - Easier maintenance and contribution

## How Benchmark Results Are Published

Despite the migration, benchmark results remain published to the same location:

1. **token-labs-performance** repo runs benchmarks against specified Dockerfile versions
2. Results are committed back to the `docs/` folder in this repo
3. GitHub Pages automatically publishes updates to [www.tokenlabs.run/benchmark-results.html](https://elizabetht.github.io/token-labs/benchmark-results.html)

**No breaking changes** - Users viewing benchmark results experience no disruption.

## Dockerfile Version Requirements

Each Dockerfile version supports different feature sets. When running benchmarks, specify the appropriate version:

- **v0.1.0** - Baseline vLLM (no special features)
- **v0.2.0** - LMCache CPU offload enabled
- **v0.3.0** - Prefix Caching + Speculative Decoding

See [DOCKERFILE_VERSIONS.md](DOCKERFILE_VERSIONS.md) for complete version documentation.

## Migration Timeline

- **December 16, 2025** - Scripts and workflows migrated to token-labs-performance
- **Backward Compatibility** - Benchmark results remain accessible at the same URLs
- **Documentation** - Updated to reference new repository structure

## Related Links

- [token-labs-performance Repository](https://github.com/elizabetht/token-labs-performance)
- [Dockerfile Version Documentation](DOCKERFILE_VERSIONS.md)
- [Benchmark Results (Live)](https://elizabetht.github.io/token-labs/benchmark-results.html)
- [Main README](../README.md)

## Questions or Issues?

If you have questions about:
- **Dockerfile builds or image issues** → Open an issue in [token-labs](https://github.com/elizabetht/token-labs)
- **Deployment or benchmark issues** → Open an issue in [token-labs-performance](https://github.com/elizabetht/token-labs-performance)

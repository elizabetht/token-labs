# Active model workloads

Only actively deployable model workloads belong here. Each model/runtime has an
independent directory and controller-runnable entrypoint where appropriate.

- `nemotron-3.5-lightning-30b-a3b-nvfp4/`: aggregate and 1P/1D manifests,
  separated into `llm-d/` and `dynamo/`.
- `qwen3-30b-a3b-instruct-2507-fp8/`: stable control manifests, separated into
  `llm-d/` and `dynamo/`.
- `BENCHMARKING.md`: controller-side deployment and comparison procedure.

Standalone experiments and retired workloads belong under `../archive/models/`.

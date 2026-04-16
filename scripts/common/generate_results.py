#!/usr/bin/env python3
import json
import os
from datetime import datetime

data = {
    'model': os.getenv('MODEL', ''),
    'dgx_cost_per_hour': float(os.getenv('DGX_COST', '0')),
    'image_tag': os.getenv('FULL_IMAGE', ''),
    'prefill': {
        'tokens_per_second': float(os.getenv('INPUT_TPS', '0')),
        'cost_per_million_tokens': float(os.getenv('COST_IN', '0')),
        'latency': {
            'mean_ttft_ms': float(os.getenv('PREFILL_MEAN_TTFT', '0')),
            'median_ttft_ms': float(os.getenv('PREFILL_MEDIAN_TTFT', '0')),
            'p99_ttft_ms': float(os.getenv('PREFILL_P99_TTFT', '0')),
            'mean_tpot_ms': float(os.getenv('PREFILL_MEAN_TPOT', '0')),
            'median_tpot_ms': float(os.getenv('PREFILL_MEDIAN_TPOT', '0')),
            'p99_tpot_ms': float(os.getenv('PREFILL_P99_TPOT', '0')),
            'mean_itl_ms': float(os.getenv('PREFILL_MEAN_ITL', '0')),
            'median_itl_ms': float(os.getenv('PREFILL_MEDIAN_ITL', '0')),
            'p99_itl_ms': float(os.getenv('PREFILL_P99_ITL', '0'))
        }
    },
    'cached': {
        'tokens_per_second': float(os.getenv('CACHED_TPS', '0')),
        'cost_per_million_tokens': float(os.getenv('COST_CACHED', '0')),
        'latency': {
            'mean_ttft_ms': float(os.getenv('CACHE_MEAN_TTFT', '0')),
            'median_ttft_ms': float(os.getenv('CACHE_MEDIAN_TTFT', '0')),
            'p99_ttft_ms': float(os.getenv('CACHE_P99_TTFT', '0')),
            'mean_tpot_ms': float(os.getenv('CACHE_MEAN_TPOT', '0')),
            'median_tpot_ms': float(os.getenv('CACHE_MEDIAN_TPOT', '0')),
            'p99_tpot_ms': float(os.getenv('CACHE_P99_TPOT', '0')),
            'mean_itl_ms': float(os.getenv('CACHE_MEAN_ITL', '0')),
            'median_itl_ms': float(os.getenv('CACHE_MEDIAN_ITL', '0')),
            'p99_itl_ms': float(os.getenv('CACHE_P99_ITL', '0'))
        }
    },
    'decode': {
        'tokens_per_second': float(os.getenv('OUTPUT_TPS', '0')),
        'cost_per_million_tokens': float(os.getenv('COST_OUT', '0')),
        'latency': {
            'mean_ttft_ms': float(os.getenv('DECODE_MEAN_TTFT', '0')),
            'median_ttft_ms': float(os.getenv('DECODE_MEDIAN_TTFT', '0')),
            'p99_ttft_ms': float(os.getenv('DECODE_P99_TTFT', '0')),
            'mean_tpot_ms': float(os.getenv('DECODE_MEAN_TPOT', '0')),
            'median_tpot_ms': float(os.getenv('DECODE_MEDIAN_TPOT', '0')),
            'p99_tpot_ms': float(os.getenv('DECODE_P99_TPOT', '0')),
            'mean_itl_ms': float(os.getenv('DECODE_MEAN_ITL', '0')),
            'median_itl_ms': float(os.getenv('DECODE_MEDIAN_ITL', '0')),
            'p99_itl_ms': float(os.getenv('DECODE_P99_ITL', '0'))
        }
    },
    'timestamp': datetime.utcnow().isoformat() + 'Z',
    'vllm_server_args': {
        'gpu_memory_utilization': float(os.getenv('GPU_MEMORY_UTILIZATION', '0.3')),
        'max_model_len': 131072,
        'kv_transfer_config': 'LMCacheConnectorV1' if os.getenv('LMCACHE_ENABLED', 'false').lower() == 'true' else None,
        'prefix_caching': os.getenv('PREFIX_CACHING_ENABLED', 'false').lower() == 'true',
        'speculative_decoding': os.getenv('SPECULATIVE_DECODING_ENABLED', 'false').lower() == 'true'
    },
    'lmcache_config': {
        'enabled': os.getenv('LMCACHE_ENABLED', 'false').lower() == 'true',
        'chunk_size': 8 if os.getenv('LMCACHE_ENABLED', 'false').lower() == 'true' else None,
        'local_cpu': True if os.getenv('LMCACHE_ENABLED', 'false').lower() == 'true' else None,
        'max_local_cpu_size': 5.0 if os.getenv('LMCACHE_ENABLED', 'false').lower() == 'true' else None
    },
    'benchmark_args': {
        'prefill_test': {
            'num_prompts': int(os.getenv('PREFILL_NUM_PROMPTS', '10')),
            'request_rate': int(os.getenv('PREFILL_REQUEST_RATE', '10')),
            'input_len': int(os.getenv('PREFILL_INPUT_LEN', '3072')),
            'output_len': int(os.getenv('PREFILL_OUTPUT_LEN', '1024')),
            'ratio': f"{os.getenv('PREFILL_INPUT_LEN', '3072')}:{os.getenv('PREFILL_OUTPUT_LEN', '1024')} input:output",
            'total_tokens': int(os.getenv('PREFILL_INPUT_LEN', '3072')) + int(os.getenv('PREFILL_OUTPUT_LEN', '1024'))
        },
        'decode_test': {
            'num_prompts': int(os.getenv('DECODE_NUM_PROMPTS', '10')),
            'request_rate': int(os.getenv('DECODE_REQUEST_RATE', '10')),
            'input_len': int(os.getenv('DECODE_INPUT_LEN', '1024')),
            'output_len': int(os.getenv('DECODE_OUTPUT_LEN', '3072')),
            'ratio': f"{os.getenv('DECODE_INPUT_LEN', '1024')}:{os.getenv('DECODE_OUTPUT_LEN', '3072')} input:output",
            'total_tokens': int(os.getenv('DECODE_INPUT_LEN', '1024')) + int(os.getenv('DECODE_OUTPUT_LEN', '3072'))
        },
        'cache_test': {
            'dataset': 'prefix_repetition',
            'num_prompts': int(os.getenv('CACHE_NUM_PROMPTS', '10')),
            'prefix_len': int(os.getenv('CACHE_PREFIX_LEN', '512')),
            'suffix_len': int(os.getenv('CACHE_SUFFIX_LEN', '128')),
            'num_prefixes': int(os.getenv('CACHE_NUM_PREFIXES', '5')),
            'output_len': int(os.getenv('CACHE_OUTPUT_LEN', '128')),
            'description': 'Tests LMCache with repeated prefixes'
        }
    },
    'hardware': {
        'platform': 'NVIDIA DGX Spark',
        'gpu': 'Grace Hopper',
        'architecture': 'ARM64'
    },
    'accuracy': {
        'ifeval': {
            'prompt_level_accuracy': float(os.getenv('IFEVAL_PROMPT_ACCURACY', '0')),
            'instruction_level_accuracy': float(os.getenv('IFEVAL_INSTRUCTION_ACCURACY', '0')),
            'num_samples': int(os.getenv('IFEVAL_NUM_SAMPLES', '0')),
            'evaluated': os.getenv('IFEVAL_EVALUATED', 'false').lower() == 'true'
        }
    }
}

with open('bench_results.json', 'w') as f:
    json.dump(data, f, indent=2)

print(json.dumps(data, indent=2))

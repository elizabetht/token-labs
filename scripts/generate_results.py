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
        'cost_per_million_tokens': float(os.getenv('COST_IN', '0'))
    },
    'cached': {
        'tokens_per_second': float(os.getenv('CACHED_TPS', '0')),
        'cost_per_million_tokens': float(os.getenv('COST_CACHED', '0'))
    },
    'decode': {
        'tokens_per_second': float(os.getenv('OUTPUT_TPS', '0')),
        'cost_per_million_tokens': float(os.getenv('COST_OUT', '0'))
    },
    'timestamp': datetime.utcnow().isoformat() + 'Z',
    'vllm_server_args': {
        'gpu_memory_utilization': 0.3,
        'max_model_len': 131072,
        'kv_transfer_config': {
            'kv_connector': 'LMCacheConnectorV1',
            'kv_role': 'kv_both'
        },
        'prefix_caching': False
    },
    'lmcache_config': {
        'enabled': True,
        'chunk_size': 8,
        'local_cpu': True,
        'max_local_cpu_size': 5.0
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
    }
}

with open('bench_results.json', 'w') as f:
    json.dump(data, f, indent=2)

print(json.dumps(data, indent=2))

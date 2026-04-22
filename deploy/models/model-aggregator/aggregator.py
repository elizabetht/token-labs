"""
Model Aggregator — returns a fixed list of models available through the token-labs gateway.
Hardcoded to show qwen35-27b and nemotron-120b; no outbound calls needed since gateway
returns empty data while backends initialize.
"""
import time
import asyncio
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

# Envoy gateway service URL (within cluster)
GATEWAY_URL = "http://envoy-token-labs-token-labs-gateway-bd0838a6.envoy-gateway-system.svc.cluster.local"
GATEWAY_HOST = "api.tokenlabs.run"

# Known models with their gateway routing headers
STATIC_MODELS = [
    {
        "id": "Qwen/Qwen3.5-27B-GPTQ-Int4",
        "object": "model",
        "owned_by": "token-labs",
        "routing_header": "Qwen/Qwen3.5-27B-GPTQ-Int4",
    },
    {
        "id": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4",
        "object": "model",
        "owned_by": "token-labs",
        "routing_header": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4",
    },
]

# Cache live model data with a 30s TTL to avoid hammering backends
_cache: list = []
_cache_ts: float = 0.0
CACHE_TTL = 30.0
_http_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def startup():
    global _http_client
    _http_client = httpx.AsyncClient(timeout=5.0)


@app.on_event("shutdown")
async def shutdown():
    global _http_client
    if _http_client:
        await _http_client.aclose()


async def fetch_live_model(routing_header: str) -> dict | None:
    """Try to get live model data from the gateway; return None on failure."""
    try:
        r = await _http_client.get(
            f"{GATEWAY_URL}/v1/models",
            headers={"Host": GATEWAY_HOST, "x-ai-eg-model": routing_header},
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                return data[0]
    except Exception:
        pass
    return None


async def get_models() -> list:
    global _cache, _cache_ts
    now = time.monotonic()
    if now - _cache_ts < CACHE_TTL and _cache:
        return _cache

    results = await asyncio.gather(*[
        fetch_live_model(m["routing_header"]) for m in STATIC_MODELS
    ])

    models = []
    for i, live in enumerate(results):
        if live:
            models.append(live)
        else:
            # Fall back to static entry (strip routing_header key)
            m = STATIC_MODELS[i]
            models.append({"id": m["id"], "object": m["object"], "owned_by": m["owned_by"]})

    _cache = models
    _cache_ts = now
    return models


@app.get("/v1/models")
async def list_models():
    data = await get_models()
    return JSONResponse({"object": "list", "data": data})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/models")
async def models_compat():
    return await list_models()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

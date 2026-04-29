import asyncio
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

GATEWAY_URL = "http://envoy-token-labs-token-labs-gateway-bd0838a6.envoy-gateway-system.svc.cluster.local"
GATEWAY_HOST = "api.tokenlabs.run"
REFRESH_INTERVAL = 28.0

STATIC_MODELS = [
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B", "object": "model", "owned_by": "token-labs", "routing_header": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"},
    {"id": "nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4", "object": "model", "owned_by": "token-labs", "routing_header": "nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4"},
]

_cache: list = [{"id": m["id"], "object": m["object"], "owned_by": m["owned_by"]} for m in STATIC_MODELS]
_http_client: httpx.AsyncClient | None = None


async def fetch_live_model(routing_header: str) -> dict | None:
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


async def refresh_cache():
    global _cache
    results = await asyncio.gather(*[fetch_live_model(m["routing_header"]) for m in STATIC_MODELS])
    models = []
    for i, live in enumerate(results):
        if live:
            models.append(live)
        else:
            m = STATIC_MODELS[i]
            models.append({"id": m["id"], "object": m["object"], "owned_by": m["owned_by"]})
    _cache = models


async def background_refresh():
    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        await refresh_cache()


@app.on_event("startup")
async def startup():
    global _http_client
    _http_client = httpx.AsyncClient(timeout=5.0)
    await refresh_cache()
    asyncio.create_task(background_refresh())


@app.on_event("shutdown")
async def shutdown():
    if _http_client:
        await _http_client.aclose()


@app.get("/v1/models")
async def list_models():
    return JSONResponse({"object": "list", "data": _cache})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/models")
async def models_compat():
    return await list_models()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

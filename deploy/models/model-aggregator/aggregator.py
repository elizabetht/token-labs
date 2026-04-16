"""
Model Aggregator — merges /v1/models from all inference backends into one response.
Serves OpenAI-compatible /v1/models so the UI sees all models in one call.
"""
import asyncio
import time
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

BACKENDS = [
    ("qwen35-27b-vllm",  "http://qwen35-27b-vllm.token-labs.svc.cluster.local:8000"),
]

async def fetch_models(client: httpx.AsyncClient, name: str, base_url: str):
    try:
        r = await client.get(f"{base_url}/v1/models", timeout=3.0)
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception:
        pass
    return []

@app.get("/v1/models")
async def list_models():
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[
            fetch_models(client, name, url) for name, url in BACKENDS
        ])
    models = []
    seen = set()
    for backend_models in results:
        for m in backend_models:
            mid = m.get("id", "")
            if mid and mid not in seen:
                seen.add(mid)
                models.append(m)
    return JSONResponse({
        "object": "list",
        "data": models,
    })

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/models")
async def models_compat():
    return await list_models()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

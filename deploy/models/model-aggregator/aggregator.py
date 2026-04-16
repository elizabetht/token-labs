"""
Model Aggregator — dynamically discovers inference backends via k8s service labels.
Any Service in the token-labs namespace with label `token-labs/model: "true"` is
automatically included. No config change needed when adding or removing models.
"""
import asyncio
import time
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from kubernetes import client, config

app = FastAPI()

NAMESPACE = "token-labs"
LABEL_SELECTOR = "token-labs/model=true"
DISCOVERY_TTL = 30  # seconds between k8s service list refreshes

_backends_cache: list = []
_backends_ts: float = 0.0


def discover_backends() -> list[tuple[str, str]]:
    """List services labelled token-labs/model=true and return (name, base_url) pairs."""
    global _backends_cache, _backends_ts
    now = time.monotonic()
    if now - _backends_ts < DISCOVERY_TTL and _backends_cache:
        return _backends_cache
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    v1 = client.CoreV1Api()
    svcs = v1.list_namespaced_service(namespace=NAMESPACE, label_selector=LABEL_SELECTOR)
    backends = [
        (
            svc.metadata.name,
            f"http://{svc.metadata.name}.{NAMESPACE}.svc.cluster.local:{svc.spec.ports[0].port}",
        )
        for svc in svcs.items
    ]
    _backends_cache = backends
    _backends_ts = now
    return backends


async def fetch_models(http_client: httpx.AsyncClient, name: str, base_url: str) -> list:
    try:
        r = await http_client.get(f"{base_url}/v1/models", timeout=3.0)
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception:
        pass
    return []


@app.get("/v1/models")
async def list_models():
    backends = discover_backends()
    async with httpx.AsyncClient() as http_client:
        results = await asyncio.gather(*[
            fetch_models(http_client, name, url) for name, url in backends
        ])
    models = []
    seen: set[str] = set()
    for backend_models in results:
        for m in backend_models:
            mid = m.get("id", "")
            if mid and mid not in seen:
                seen.add(mid)
                models.append(m)
    return JSONResponse({"object": "list", "data": models})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/models")
async def models_compat():
    return await list_models()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

from __future__ import annotations

import httpx


async def test_health_reports_a_status(client: httpx.AsyncClient) -> None:
    """Health answers even with no database — it reports degraded instead of 500."""
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["embedding_dim"] == 1024
    assert "reachable" in body["database"]


async def test_root(client: httpx.AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "aira-api"

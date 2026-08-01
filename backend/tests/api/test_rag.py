from __future__ import annotations

from app.services.rag_service import RagUnavailable


async def test_rag_routes_require_authentication(client):
    response = await client.get("/api/v1/rag/status")
    assert response.status_code == 401


async def test_rag_status_reports_bundled_assets(client, farmer):
    response = await client.get("/api/v1/rag/status", headers=farmer.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["vector_store_found"] is True


async def test_rag_question_returns_source_grounded_answer(
    client, farmer, app, monkeypatch
):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def unavailable_semantic():
        raise RagUnavailable("forced fallback")

    monkeypatch.setattr(app.state.rag_service, "_ensure_vector_store", unavailable_semantic)

    response = await client.post(
        "/api/v1/rag/query",
        json={"question": "Kuru incirde aflatoksin nasıl önlenir?"},
        headers=farmer.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"]
    assert body["sources"]
    assert body["retrieval_mode"] == "keyword-fallback"


async def test_rag_rejects_unknown_model_decision(client, farmer):
    response = await client.post(
        "/api/v1/rag/inspection-advice",
        json={"decision": "Unknown"},
        headers=farmer.headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

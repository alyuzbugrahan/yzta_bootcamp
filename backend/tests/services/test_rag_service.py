from __future__ import annotations

import pytest

from app.config import RagSettings
from app.services.rag_service import RagService, RagUnavailable


@pytest.fixture
def service(monkeypatch) -> RagService:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    instance = RagService(RagSettings())

    def unavailable_semantic():
        raise RagUnavailable("semantic dependencies unavailable in unit test")

    monkeypatch.setattr(instance, "_ensure_vector_store", unavailable_semantic)
    return instance


def test_status_finds_the_bundled_vector_store(service):
    status = service.status()

    assert status["enabled"] is True
    assert status["vector_store_found"] is True
    assert status["sources_found"] is True


@pytest.mark.asyncio
async def test_question_uses_keyword_fallback_and_returns_sources(service):
    result = await service.answer_question("Kuru incirde aflatoksin nasıl önlenir?")

    assert result.retrieval_mode == "keyword-fallback"
    assert result.generation_mode == "extractive"
    assert result.sources
    assert "Kaynaklarda" in result.answer


@pytest.mark.asyncio
async def test_aflatoxin_advice_is_marked_as_urgent(service):
    result = await service.inspection_advice("Aflatoxin", confidence=0.91)

    assert result.answer.startswith("⚠️")
    assert "%91.0" in result.answer
    assert result.sources


@pytest.mark.asyncio
async def test_healthy_advice_is_supported(service):
    result = await service.inspection_advice("Healthy")

    assert result.answer.startswith("✅")
    assert result.sources


@pytest.mark.asyncio
async def test_empty_question_is_rejected(service):
    with pytest.raises(ValueError, match="empty"):
        await service.answer_question("   ")

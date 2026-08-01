"""RAG assistant endpoints used by the web client."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.errors import ApiError, ErrorCode, NotFound
from app.core.logging import get_logger
from app.deps import CurrentUser
from app.services.rag_service import RagResult, RagUnavailable

log = get_logger(__name__)
router = APIRouter(tags=["rag"])

_DOCUMENT_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
}


class RagQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=5000)


class RagAdviceRequest(BaseModel):
    decision: str = Field(min_length=1, max_length=32)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class RagSourceResponse(BaseModel):
    source: str
    snippet: str
    page: int | None = None
    score: float | None = None
    url: str | None = None


class RagAnswerResponse(BaseModel):
    answer: str
    sources: list[RagSourceResponse]
    retrieval_mode: str
    generation_mode: str


class RagStatusResponse(BaseModel):
    enabled: bool
    vector_store_found: bool
    sources_found: bool
    semantic_dependencies: bool
    llm_configured: bool
    embedding_model: str
    llm_model: str


def _response(result: RagResult, request: Request) -> RagAnswerResponse:
    rag_service = request.app.state.rag_service
    return RagAnswerResponse(
        answer=result.answer,
        sources=[
            RagSourceResponse(
                source=item.source,
                snippet=item.snippet,
                page=item.page,
                score=item.score,
                # Only ever populated when the file genuinely exists on disk, so the
                # client never renders a link that leads nowhere.
                url=rag_service.document_url(item),
            )
            for item in result.sources
        ],
        retrieval_mode=result.retrieval_mode,
        generation_mode=result.generation_mode,
    )


@router.get("/rag/status", response_model=RagStatusResponse)
async def rag_status(request: Request, _user: CurrentUser) -> RagStatusResponse:
    return RagStatusResponse.model_validate(request.app.state.rag_service.status())


@router.post("/rag/query", response_model=RagAnswerResponse)
async def rag_query(
    payload: RagQuestionRequest,
    request: Request,
    _user: CurrentUser,
) -> RagAnswerResponse:
    try:
        result = await request.app.state.rag_service.answer_question(payload.question)
        return _response(result, request)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(exc),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ) from exc
    except RagUnavailable as exc:
        raise ApiError(
            ErrorCode.RAG_UNAVAILABLE,
            str(exc),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - hide provider details from clients
        log.exception("rag_query_failed")
        raise ApiError(
            ErrorCode.RAG_QUERY_FAILED,
            "RAG yanıtı oluşturulamadı",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc


@router.post("/rag/inspection-advice", response_model=RagAnswerResponse)
async def inspection_advice(
    payload: RagAdviceRequest,
    request: Request,
    _user: CurrentUser,
) -> RagAnswerResponse:
    try:
        result = await request.app.state.rag_service.inspection_advice(
            payload.decision,
            payload.confidence,
        )
        return _response(result, request)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(exc),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ) from exc
    except RagUnavailable as exc:
        raise ApiError(
            ErrorCode.RAG_UNAVAILABLE,
            str(exc),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("rag_advice_failed")
        raise ApiError(
            ErrorCode.RAG_QUERY_FAILED,
            "RAG önerisi oluşturulamadı",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc


@router.get("/rag/documents/{filename}")
async def get_rag_document(
    filename: str,
    request: Request,
    _user: CurrentUser,
) -> FileResponse:
    """Serve one file from the bundled source pool so answers can link to it.

    Only filenames that resolve to a real file directly inside the RAG sources
    directory are served (``RagService.document_path`` rejects traversal and missing
    files), and only recognised document types are exposed — this is reference
    material shared with every authenticated user, not per-farmer data.
    """
    rag_service = request.app.state.rag_service
    path = rag_service.document_path(filename)
    if path is None or path.suffix.lower() not in _DOCUMENT_MEDIA_TYPES:
        raise NotFound("Document not found")

    return FileResponse(
        path,
        media_type=_DOCUMENT_MEDIA_TYPES[path.suffix.lower()],
        filename=path.name,
        headers={"Cache-Control": "private, max-age=3600"},
    )
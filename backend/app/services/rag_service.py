"""Retrieval-augmented guidance for dried-fig producers and exporters.

The service wraps the vector store supplied with the project. Heavy embedding and Chroma imports
are lazy: live scanning does not pay their startup cost, and the API can still boot with RAG
disabled. When the semantic stack is unavailable, a deterministic keyword fallback reads the
bundled Chroma metadata so the assistant remains usable instead of failing the whole application.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from app.config import RagSettings
log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-zA-ZçğıöşüÇĞİÖŞÜ0-9]+")
_AFLATOXIN_TERMS = "aflatoksin kurutma nem kerevet depo sergi bulaşma ayırma önleme"


class RagUnavailable(RuntimeError):
    """The configured RAG assets or dependencies cannot be used."""


@dataclass(frozen=True, slots=True)
class RetrievedSource:
    source: str
    snippet: str
    page: int | None = None
    score: float | None = None


@dataclass(frozen=True, slots=True)
class RagResult:
    answer: str
    sources: list[RetrievedSource]
    retrieval_mode: str
    generation_mode: str


class RagService:
    def __init__(self, settings: RagSettings, project_root: Path | None = None) -> None:
        self.settings = settings
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self.vector_store_path = self._resolve(settings.vector_store_path)
        self.sources_path = self._resolve(settings.sources_path)
        self._vector_store: Any | None = None
        self._init_lock = Lock()
        self._google_client: Any | None = None

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (self.project_root / path).resolve()

    def document_path(self, filename: str) -> Path | None:
        """Resolve a source filename to a real file inside ``sources_path``.

        Only a bare filename (no path separators, no traversal) that resolves to an
        existing regular file directly inside the sources directory is accepted. Used to
        decide whether a source in an answer can safely be linked to, and to serve it.
        """
        if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
            return None
        candidate = (self.sources_path / filename).resolve()
        try:
            candidate.relative_to(self.sources_path.resolve())
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def document_url(self, source: "RetrievedSource") -> str | None:
        """Build a link to a source's original document, only when it truly exists.

        Returns ``None`` (never a guessed or broken link) when the file cannot be found,
        so the client only shows a link when it actually leads somewhere.
        """
        path = self.document_path(source.source)
        if path is None:
            return None
        url = f"/rag/documents/{path.name}"
        if source.page is not None and path.suffix.lower() == ".pdf":
            url += f"#page={source.page}"
        return url

    def status(self) -> dict[str, object]:
        sqlite_path = self.vector_store_path / "chroma.sqlite3"
        semantic_dependencies = all(
            importlib.util.find_spec(name) is not None
            for name in ("langchain_chroma", "langchain_huggingface", "sentence_transformers")
        )
        api_key = self._api_key()
        return {
            "enabled": self.settings.enabled,
            "vector_store_found": sqlite_path.is_file(),
            "sources_found": self.sources_path.is_dir(),
            "semantic_dependencies": semantic_dependencies,
            "llm_configured": bool(api_key),
            "embedding_model": self.settings.embedding_model,
            "llm_model": self.settings.llm_model,
        }

    async def answer_question(self, question: str) -> RagResult:
        question = self._validate_question(question)
        sources, retrieval_mode = await asyncio.to_thread(self._retrieve, question)
        if not sources:
            return RagResult(
                answer=(
                    "Bu soru için doğrulanmış belge havuzunda yeterli bilgi bulunamadı. "
                    "Soruyu farklı kelimelerle yeniden yazmayı deneyin."
                ),
                sources=[],
                retrieval_mode=retrieval_mode,
                generation_mode="extractive",
            )

        answer, generation_mode = await asyncio.to_thread(
            self._generate_answer,
            question,
            sources,
            False,
        )
        return RagResult(answer, sources, retrieval_mode, generation_mode)

    async def inspection_advice(
        self,
        decision: str,
        confidence: float | None = None,
    ) -> RagResult:
        normalised = decision.strip().casefold()
        is_aflatoxin = normalised in {
            "aflatoxin",
            "aflatoksin",
            "aflatoksin var",
            "risk",
            "positive",
        }
        is_healthy = normalised in {
            "healthy",
            "sağlıklı",
            "saglikli",
            "aflatoksin yok",
            "negative",
        }
        if not (is_aflatoxin or is_healthy):
            raise ValueError("decision must be Aflatoxin or Healthy")

        if is_aflatoxin:
            query = _AFLATOXIN_TERMS
            question = (
                "Aflatoksin riski saptanan kuru incir partisi için acil ayırma, kurutma, nem "
                "ve depolama adımları nelerdir?"
            )
        else:
            query = "aflatoksin önleme uygun kurutma nem depolama sağlıklı incir"
            question = (
                "Aflatoksin saptanmayan kuru incir partisinde güvenli kurutma ve depolama "
                "koşulları nasıl korunmalıdır?"
            )

        sources, retrieval_mode = await asyncio.to_thread(self._retrieve, query)
        answer, generation_mode = await asyncio.to_thread(
            self._generate_answer,
            question,
            sources,
            is_aflatoxin,
        )

        confidence_text = ""
        if confidence is not None:
            confidence_text = f"\n\nModel güven skoru: %{confidence * 100:.1f}."

        if is_aflatoxin:
            prefix = (
                "⚠️ Üründe aflatoksin riski saptandı. Partiyi sağlam ürünlerden "
                "ayırın ve aşağıdaki kaynak-temelli adımları uygulayın.\n\n"
            )
        else:
            prefix = (
                "✅ Üründe aflatoksin bulgusu saptanmadı. Mevcut iyi uygulamaları sürdürmek "
                "için aşağıdaki koruyucu adımları izleyin.\n\n"
            )

        return RagResult(
            prefix + answer + confidence_text,
            sources,
            retrieval_mode,
            generation_mode,
        )

    def _validate_question(self, question: str) -> str:
        if not self.settings.enabled:
            raise RagUnavailable("RAG assistant is disabled")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question cannot be empty")
        question = question.strip()
        if len(question) > self.settings.max_question_length:
            raise ValueError(
                f"question cannot exceed {self.settings.max_question_length} characters"
            )
        return question

    def _retrieve(self, query: str) -> tuple[list[RetrievedSource], str]:
        if not self.settings.enabled:
            raise RagUnavailable("RAG assistant is disabled")
        if not (self.vector_store_path / "chroma.sqlite3").is_file():
            raise RagUnavailable(f"Vector store not found: {self.vector_store_path}")

        try:
            store = self._ensure_vector_store()
            results = store.similarity_search_with_score(query, k=self.settings.search_k)
            sources = [
                RetrievedSource(
                    source=self._normalise_source(doc.metadata.get("source")),
                    page=self._normalise_page(doc.metadata.get("page")),
                    snippet=self._clean_snippet(doc.page_content),
                    score=round(float(score), 4),
                )
                for doc, score in results
                if getattr(doc, "page_content", "").strip()
            ]
            return self._dedupe(sources), "semantic"
        except Exception as exc:  # noqa: BLE001 - fallback is intentional and logged
            log.warning("rag_semantic_fallback: %s", exc)
            return self._keyword_retrieve(query), "keyword-fallback"

    def _ensure_vector_store(self):
        if self._vector_store is not None:
            return self._vector_store

        with self._init_lock:
            if self._vector_store is not None:
                return self._vector_store

            try:
                from langchain_chroma import Chroma
                from langchain_huggingface import HuggingFaceEmbeddings
            except ImportError as exc:
                raise RagUnavailable(
                    "Semantic RAG dependencies are missing; install the project with the rag extra"
                ) from exc

            embeddings = HuggingFaceEmbeddings(
                model_name=self.settings.embedding_model,
                model_kwargs={"device": "cpu"},
            )
            self._vector_store = Chroma(
                collection_name=self.settings.collection_name,
                persist_directory=str(self.vector_store_path),
                embedding_function=embeddings,
            )
            return self._vector_store

    def _keyword_retrieve(self, query: str) -> list[RetrievedSource]:
        """Read Chroma metadata and rank chunks without external ML dependencies."""
        database = self.vector_store_path / "chroma.sqlite3"
        terms = self._tokens(query)
        if not terms:
            return []

        rows: dict[int, dict[str, object]] = {}
        with sqlite3.connect(database) as connection:
            for row_id, key, string_value, int_value in connection.execute(
                """
                SELECT id, key, string_value, int_value
                FROM embedding_metadata
                WHERE key IN ('chroma:document', 'source', 'page')
                """
            ):
                item = rows.setdefault(row_id, {})
                item[key] = int_value if key == "page" else string_value

        ranked: list[tuple[float, RetrievedSource]] = []
        for item in rows.values():
            document = str(item.get("chroma:document") or "").strip()
            if not document:
                continue
            document_tokens = self._tokens(document)
            overlap = terms & document_tokens
            if not overlap:
                continue
            frequency = sum(document.casefold().count(term) for term in overlap)
            score = len(overlap) * 3.0 + min(frequency, 10) * 0.35
            ranked.append(
                (
                    score,
                    RetrievedSource(
                        source=self._normalise_source(item.get("source")),
                        page=self._normalise_page(item.get("page")),
                        snippet=self._clean_snippet(document),
                        score=round(score, 4),
                    ),
                )
            )

        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return self._dedupe([source for _, source in ranked[: self.settings.search_k]])

    def _generate_answer(
        self,
        question: str,
        sources: list[RetrievedSource],
        urgent: bool,
    ) -> tuple[str, str]:
        if sources and self._api_key() and importlib.util.find_spec("google.genai") is not None:
            try:
                return self._generate_with_gemini(question, sources, urgent), "gemini"
            except Exception as exc:  # noqa: BLE001 - source-only fallback must remain available
                log.warning("rag_llm_fallback: %s", exc)

        return self._extractive_answer(sources, urgent), "extractive"

    def _generate_with_gemini(
        self,
        question: str,
        sources: list[RetrievedSource],
        urgent: bool,
    ) -> str:
        from google import genai
        from google.genai import types

        if self._google_client is None:
            self._google_client = genai.Client(api_key=self._api_key())

        context = "\n\n".join(
            f"[Kaynak {index}: {source.source}]\n{source.snippet}"
            for index, source in enumerate(sources, start=1)
        )
        system_instruction = (
            "Sen kuru incir üreticileri ve ihracatçıları için çalışan bir tarım destek "
            "asistanısın. Yalnızca verilen kaynak metinlerde açıkça bulunan bilgilere dayan. "
            "Kaynakta olmayan sayı, mevzuat veya uygulama uydurma. "
            "Yalnızca kullanıcının sorduğu soruyu yanıtla. Kaynak metinlerde soruyla "
            "doğrudan ilgisi olmayan başka bilgiler (farklı bir yöntem, farklı bir konu vb.) "
            "geçse bile bunları cevaba katma; sadece sorulan konuya odaklan ve gereksiz yere "
            "uzatma. "
            "Yanıtı sade ve akıcı Türkçe ile, birbirine bağlı düzgün cümleler ve paragraflar "
            "halinde yaz; bir kişiye anlatır gibi konuş. "
            "Kesinlikle markdown biçimlendirmesi kullanma: yıldız (*), çift yıldız (**), "
            "tire (-), başlık (#), numaralı liste veya madde imi kullanma; düz metin yaz. "
            "Adımları veya öğeleri madde madde değil, 've', 'ardından', 'bunun yanında' gibi "
            "bağlaçlarla cümle içinde sırala. "
            "Yanıtın içine [Kaynak N] veya (Kaynak N) gibi kaynak numarası ibareleri EKLEME; "
            "kaynaklar kullanıcıya cevabın altında ayrıca ve otomatik olarak gösteriliyor. "
            "Tıbbi tanı veya laboratuvar doğrulaması yaptığını iddia etme."
        )
        urgency = (
            "Risk durumunda önce acil ayırma ve bulaşmayı önleme adımlarını sırala."
            if urgent
            else "Koruyucu ve sürdürülebilir uygulamaları öne çıkar."
        )
        prompt = f"{urgency}\n\nKAYNAKLAR:\n{context}\n\nSORU:\n{question}"
        response = self._google_client.models.generate_content(
            model=self.settings.llm_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Gemini returned an empty answer")
        return self._strip_markdown(text)

    @staticmethod
    def _extractive_answer(sources: list[RetrievedSource], urgent: bool) -> str:
        if not sources:
            return (
                "İlgili kaynak parçası bulunamadığı için güvenilir bir öneri "
                "üretilemedi. Belge havuzunu kontrol edin."
            )

        heading = (
            "Kaynaklarda öne çıkan acil bilgiler şunlardır:"
            if urgent
            else "Kaynaklarda öne çıkan bilgiler şunlardır:"
        )
        sentences = [heading]
        for source in sources[:4]:
            snippet = source.snippet.rstrip()
            if snippet and snippet[-1] not in ".!?":
                snippet += "."
            sentences.append(snippet)
        return " ".join(sentences)

    def _api_key(self) -> str:
        return self.settings.api_key.strip() or os.getenv("GOOGLE_API_KEY", "").strip()

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token.casefold()
            for token in _TOKEN_RE.findall(text)
            if len(token) > 2
        }

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Güvenlik ağı: modelin talimata uymayıp markdown döndürmesi ihtimaline karşı
        yıldız/tire/başlık/numaralı liste işaretlerini temizleyip düz, akıcı paragraf
        metnine çevirir. Frontend markdown render etmediği için ham ** / * karakterleri
        kullanıcıya çıplak şekilde görünür; bu yüzden burada temizlenir.
        """
        cleaned_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Başlık işaretlerini kaldır (#, ##, ...)
            line = re.sub(r"^#{1,6}\s*", "", line)
            # Madde imlerini (*, -, •) ve numaralı liste önekini (1. 2) kaldır
            line = re.sub(r"^[\*\-•]\s+", "", line)
            line = re.sub(r"^\d+[\.\)]\s+", "", line)
            # Kalın/italik yıldızları kaldır, metni koru (**metin** / *metin* -> metin)
            line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
            line = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", line)
            # Kalan tekil yıldız/alt çizgi vurgu karakterlerini temizle
            line = line.replace("**", "").replace("__", "")
            line = re.sub(r"(?<!\S)\*(?!\S)", "", line)
            # Kalan [Kaynak N] / (Kaynak N) ibarelerini tamamen kaldır; kaynaklar artık
            # cevabın altında ayrıca listeleniyor, metin içinde tekrar etmesine gerek yok.
            line = re.sub(r"[\[\(]\s*Kaynak\s*\d+[^\]\)]*[\]\)]", "", line)
            line = re.sub(r"\s+([.,;:])", r"\1", line)
            if line:
                cleaned_lines.append(line)

        if not cleaned_lines:
            return text.strip()

        # Kısa madde satırlarını tek bir akıcı paragrafta birleştir.
        paragraph = " ".join(cleaned_lines)
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        return paragraph

    @staticmethod
    def _clean_snippet(text: str, limit: int = 900) -> str:
        clean = " ".join(str(text).split())
        return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"

    @staticmethod
    def _normalise_source(value: object) -> str:
        if not value:
            return "Bilinmeyen kaynak"
        return Path(str(value).replace("\\", "/")).name

    @staticmethod
    def _normalise_page(value: object) -> int | None:
        try:
            return int(value) + 1 if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _dedupe(sources: list[RetrievedSource]) -> list[RetrievedSource]:
        unique: list[RetrievedSource] = []
        seen: set[tuple[str, str]] = set()
        for source in sources:
            key = (source.source, source.snippet)
            if key not in seen:
                seen.add(key)
                unique.append(source)
        return unique
   
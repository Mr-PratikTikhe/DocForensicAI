import re
import logging
import time
from dataclasses import dataclass

from django.conf import settings

from assistant.models import DocumentChunk

from .embeddings import cosine_similarity, embed_query, local_embedding


logger = logging.getLogger(__name__)


NOT_FOUND_MESSAGE = "I couldn't find that information in the selected documents."
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "how", "i", "in", "is", "it", "of", "on", "or", "that",
    "the", "this", "to", "was", "what", "when", "where", "which", "who", "why", "with",
    "about", "pdf", "document", "file", "overview", "summarize", "summary", "describe",
}
OVERVIEW_TERMS = {"about", "overview", "summarize", "summary", "describe"}
SYNTHESIS_TERMS = {
    "compare", "comparison", "differences", "contradiction", "contradictions",
    "gap", "gaps", "quiz", "flashcard", "flashcards", "literature", "knowledge",
    "map", "explain", "concepts",
}
FEATURE_RETRIEVAL_QUERIES = {
    "compare": "theory concepts principles results applications limitations",
    "quiz": "key concepts findings principles definitions",
    "flashcards": "key concepts findings principles definitions",
    "explain": "key concepts findings principles definitions",
    "literature_review": "summary findings methods results conclusions",
    "research_gaps": "limitations future work findings conclusions",
    "knowledge_map": "summary themes concepts findings conclusions",
}
FEATURE_INSTRUCTIONS = {
    "compare": (
        "Compare the selected documents using only the supplied passages. Start with a short scope note naming each document and "
        "the subject visible in its excerpts. Then use headings for Shared points, Differences in focus or approach, and Direct "
        "contradictions. Do not invent a shared claim: if the excerpts cover different subjects, say they are not directly comparable "
        "on a claim-by-claim basis and explain what each excerpt does establish. A lack of a matching passage is not itself a "
        "contradiction. When at least one passage from each selected document is present, provide this bounded comparison instead "
        "of returning INSUFFICIENT_CONTEXT. Cite every point with [S#] and name the source document."
    ),
    "quiz": (
        "Create exactly 5 multiple-choice questions from the passages. For each, show four choices labeled A–D, then the "
        "correct answer, a short explanation, and its [S#] citation. Format each question as a ### heading, with each choice on its "
        "own line. Put answers directly after each question. Do not test outside facts."
    ),
    "flashcards": (
        "Create 8 concise flashcards from the passages. Format each with a Question heading, Answer, and [S#] citation. "
        "Use only facts stated in the passages."
    ),
    "explain": (
        "Explain the central concepts in plain language. Define technical terms and give a short source-grounded explanation "
        "for each, citing each claim with [S#]. Do not add outside facts."
    ),
    "literature_review": (
        "Write a concise literature review grounded only in these passages. Synthesize shared themes, describe each paper's "
        "contribution when supported, and distinguish evidence from interpretation. Cite every factual claim with [S#]."
    ),
    "research_gaps": (
        "Identify (1) explicit contradictions and (2) research gaps or topics these passages do not address. Keep these categories "
        "separate, avoid treating missing context as proof, explain the evidence limit, and cite relevant claims with [S#]."
    ),
    "knowledge_map": (
        "Build a compact knowledge map from the passages. List recurring concepts and explain only source-supported links between them. "
        "Cite each concept and connection with [S#]. Do not imply the papers cover the entire field."
    ),
}


@dataclass
class RetrievedChunk:
    chunk: DocumentChunk
    score: float


def retrieve(
    question: str,
    document_ids: list[int] | None = None,
    limit: int = 5,
    feature_mode: str | None = None,
    retrieval_query_override: str | None = None,
) -> list[RetrievedChunk]:
    queryset = DocumentChunk.objects.select_related("document").filter(document__status="ready")
    if document_ids:
        queryset = queryset.filter(document_id__in=document_ids)
    chunks = list(queryset)
    if not chunks:
        return []

    query_vectors: dict[str, list[float]] = {}
    ranked: list[RetrievedChunk] = []
    retrieval_query = retrieval_query_override or FEATURE_RETRIEVAL_QUERIES.get(feature_mode) or (
        "summary overview" if (_is_overview_question(question) or _is_synthesis_request(question)) else question
    )
    for chunk in chunks:
        provider = chunk.embedding_provider
        if provider not in query_vectors:
            try:
                query_vectors[provider] = embed_query(retrieval_query, provider)
            except Exception as exc:
                if not provider.startswith("gemini:"):
                    raise
                logger.warning(
                    "Query embedding unavailable for provider=%s (%s); converting selected document chunks to local embeddings",
                    provider,
                    type(exc).__name__,
                )
                provider_chunks = [item for item in chunks if item.embedding_provider == provider]
                for item in provider_chunks:
                    item.embedding = local_embedding(item.text)
                    item.embedding_provider = "local-hash"
                DocumentChunk.objects.bulk_update(provider_chunks, ["embedding", "embedding_provider"])
                query_vectors["local-hash"] = local_embedding(retrieval_query)
                provider = "local-hash"
        else:
            provider = chunk.embedding_provider
        score = cosine_similarity(query_vectors[provider], chunk.embedding)
        if feature_mode == "compare" and provider == "local-hash":
            text_length = max(1, len(chunk.text))
            readable_ratio = sum(character.isalpha() for character in chunk.text) / text_length
            # Local hash vectors can rank equation-heavy fragments and noisy
            # OCR above prose. Prefer readable passages for human comparison.
            score *= max(0.05, readable_ratio - 0.15)
        ranked.append(RetrievedChunk(chunk=chunk, score=score))
    ranked.sort(key=lambda item: item.score, reverse=True)
    # When querying multiple papers, reserve a citation slot for each paper
    # before filling the remaining slots by relevance. This gives comparison
    # prompts evidence from both sides instead of five passages from one file.
    if feature_mode == "compare" and document_ids and len(set(document_ids)) > 1:
        # Compare features need evidence from every selected paper and should
        # avoid using duplicate passages from the same page as separate sources.
        buckets: dict[int, list[RetrievedChunk]] = {}
        for item in ranked:
            buckets.setdefault(item.chunk.document_id, []).append(item)
        balanced: list[RetrievedChunk] = []
        ordered_ids = list(dict.fromkeys(document_ids))
        for depth in range(3):
            for doc_id in ordered_ids:
                candidates = buckets.get(doc_id, [])
                pages = {item.chunk.page_number for item in balanced if item.chunk.document_id == doc_id}
                candidate = next(
                    (item for item in candidates if item.chunk.page_number not in pages and _usable_comparison_passage(item.chunk.text)),
                    None,
                )
                if candidate:
                    balanced.append(candidate)
        ranked = balanced or ranked
        limit = max(limit, min(len(ordered_ids) * 3, 12))
    elif document_ids and len(set(document_ids)) > 1:
        diversified: list[RetrievedChunk] = []
        seen_documents: set[int] = set()
        for item in ranked:
            if item.chunk.document_id not in seen_documents:
                diversified.append(item)
                seen_documents.add(item.chunk.document_id)
        chosen_ids = {item.chunk.id for item in diversified}
        diversified.extend(item for item in ranked if item.chunk.id not in chosen_ids)
        ranked = diversified
    return ranked[:limit]


def _usable_comparison_passage(text: str) -> bool:
    cleaned = text.strip()
    if len(cleaned) < 120:
        return False
    if re.search(r"(?:\.{4,}|_{4,})", cleaned):
        return False
    letters = sum(character.isalpha() for character in cleaned)
    spaced_letter_runs = re.findall(r"(?:\b[A-Za-z]\s+){3,}", cleaned)
    return letters > 0 and sum(len(run) for run in spaced_letter_runs) / letters < 0.18


def answer_question(
    question: str,
    document_ids: list[int] | None = None,
    feature_mode: str | None = None,
) -> dict:
    started_at = time.perf_counter()
    retrieval_started_at = started_at
    results = retrieve(question, document_ids, feature_mode=feature_mode)
    logger.info(
        "Document retrieval completed in %.2fs (%s passages selected, mode=%s)",
        time.perf_counter() - retrieval_started_at,
        len(results),
        feature_mode or "chat",
    )
    if not results or not _has_enough_evidence(question, results, feature_mode):
        return _not_found_payload()

    sources = [_serialize_source(item, index + 1) for index, item in enumerate(results)]
    if settings.GEMINI_API_KEY:
        try:
            generation_started_at = time.perf_counter()
            answer = _generate_with_gemini(question, sources, feature_mode)
            logger.info("Gemini generation completed in %.2fs using %s", time.perf_counter() - generation_started_at, settings.GEMINI_MODEL)
            if not answer:
                return _not_found_payload()
            if "INSUFFICIENT_CONTEXT" in answer:
                if feature_mode == "compare" and _has_comparison_evidence(sources, document_ids):
                    answer = _comparison_scope_answer(sources, document_ids)
                    provider = f"Grounded comparison summary · {settings.GEMINI_MODEL}"
                else:
                    return _not_found_payload()
            else:
                provider = f"Gemini · {settings.GEMINI_MODEL}"
        except Exception as exc:
            logger.exception("Gemini generation failed for model=%s feature_mode=%s", settings.GEMINI_MODEL, feature_mode or "chat")
            fallback_model = getattr(settings, "GEMINI_FALLBACK_MODEL", "")
            if _is_temporary_unavailable(exc) and fallback_model and fallback_model != settings.GEMINI_MODEL:
                try:
                    logger.warning("Retrying Gemini request with fallback model=%s", fallback_model)
                    fallback_started_at = time.perf_counter()
                    answer = _generate_with_gemini(question, sources, feature_mode, model=fallback_model)
                    logger.info("Gemini fallback completed in %.2fs using %s", time.perf_counter() - fallback_started_at, fallback_model)
                    if not answer:
                        return _not_found_payload()
                    if "INSUFFICIENT_CONTEXT" in answer:
                        if feature_mode == "compare" and _has_comparison_evidence(sources, document_ids):
                            answer = _comparison_scope_answer(sources, document_ids)
                            provider = f"Grounded comparison summary · {fallback_model}"
                        else:
                            return _not_found_payload()
                    else:
                        provider = f"Gemini fallback · {fallback_model}"
                except Exception as fallback_exc:
                    logger.exception("Gemini fallback failed for model=%s feature_mode=%s", fallback_model, feature_mode or "chat")
                    if feature_mode:
                        return {
                            "answer": _gemini_failure_message(fallback_exc, fallback_attempted=True),
                            "found": False,
                            "sources": [],
                            "provider": "Gemini fallback error",
                        }
                    answer = _extractive_answer(question, results)
                    provider = "Local extractive fallback"
            elif feature_mode:
                return {
                    "answer": _gemini_failure_message(exc),
                    "found": False,
                    "sources": [],
                    "provider": "Gemini generation error",
                }
    else:
        if feature_mode:
            return {
                "answer": "This research tool needs Gemini to generate a structured result. The current local mode can retrieve passages but cannot safely create this output.",
                "found": False,
                "sources": sources,
                "provider": "Gemini required for structured feature",
            }
        answer = _extractive_answer(question, results)
        provider = "Local extractive fallback"

    if not answer:
        return _not_found_payload()
    logger.info("Answer completed in %.2fs via %s", time.perf_counter() - started_at, provider)
    return {"answer": answer, "found": True, "sources": sources, "provider": provider}


def _has_comparison_evidence(sources: list[dict], document_ids: list[int] | None) -> bool:
    if not document_ids or len(set(document_ids)) < 2:
        return False
    source_document_ids = {source["document_id"] for source in sources if len(source.get("text", "").strip()) >= 120}
    return set(document_ids).issubset(source_document_ids)


def _comparison_scope_answer(sources: list[dict], document_ids: list[int]) -> str:
    by_document: dict[int, list[dict]] = {}
    for source in sources:
        by_document.setdefault(source["document_id"], []).append(source)
    lines = [
        "The retrieved passages support a comparison of document scope, but not a reliable claim-by-claim comparison or a finding of contradiction.",
        "### Document coverage",
    ]
    for doc_id in document_ids:
        doc_sources = by_document.get(doc_id, [])
        if not doc_sources:
            continue
        name = doc_sources[0]["document"]
        best_source = max(
            doc_sources,
            key=lambda source: source["score"] * (sum(ch.isalpha() for ch in source["text"]) / max(1, len(source["text"]))),
        )
        excerpt = re.sub(r"\s+", " ", best_source["text"]).strip()
        if len(excerpt) > 390:
            excerpt = excerpt[:387].rsplit(" ", 1)[0] + "…"
        lines.append(f"- **{name}** ({best_source['location']}): {excerpt} [S{best_source['id']}]")
    lines.extend([
        "### What can be concluded",
        "These excerpts show the topics covered in the selected passages. They do not establish whether the documents agree or conflict on a shared claim. Select passages about the same topic, or ask a narrower comparison question, for a direct comparison.",
    ])
    return "\n".join(lines)


def _has_enough_evidence(
    question: str,
    results: list[RetrievedChunk],
    feature_mode: str | None = None,
) -> bool:
    top = results[0]
    if top.chunk.embedding_provider == "local-hash":
        if feature_mode:
            return top.score >= 0.05
        query_terms = _meaningful_terms(question)
        if _is_synthesis_request(question):
            return top.score >= 0.05
        # Broad overview questions have no useful keyword to overlap with a
        # document. Local hash vectors still provide a weak ranking signal;
        # accept only a stronger match for these questions.
        if not query_terms and _is_overview_question(question):
            return top.score >= 0.05
        return any(
            bool(query_terms & _meaningful_terms(item.chunk.text)) and item.score >= 0.055
            for item in results
        )
    return top.score >= 0.30


def _generate_with_gemini(
    question: str,
    sources: list[dict],
    feature_mode: str | None = None,
    model: str | None = None,
) -> str:
    from google import genai
    from google.genai import types

    context = "\n\n".join(
        f"[S{source['id']}] {source['document']} ({source['location']})\n{source['text']}"
        for source in sources
    )
    output_token_limits = {
        "compare": 1000,
        "quiz": 1600,
        "flashcards": 1200,
        "explain": 700,
        "literature_review": 1100,
        "research_gaps": 900,
        "knowledge_map": 900,
    }
    output_token_limit = output_token_limits.get(feature_mode, 700)
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=model or settings.GEMINI_MODEL,
        contents=(
            f"Question: {question}\n\nRetrieved sources:\n{context}\n\n"
            "Write a direct answer to the question. Synthesize relevant evidence across sources; do not copy whole passages, "
            "repeat the question, or list source titles as the answer. Use a short opening followed by bullets or numbered steps "
            "when that improves readability. Cite each factual point inline using the supplied [S#] labels. End with a brief "
            "scope note only when the retrieved passages leave a material gap."
        ),
        config=types.GenerateContentConfig(
            system_instruction=(
                "You are a document research assistant. Answer only from the retrieved source text; do not infer unsupported facts "
                "or use outside knowledge. Cite each factual claim with the provided [S1], [S2], etc. labels. If the sources do not "
                "answer the question, output exactly INSUFFICIENT_CONTEXT. Be clear, useful, and concise, explain technical language "
                "plainly, preserve important qualifications or uncertainty from the source, and format with short headings and lists. "
                f"{FEATURE_INSTRUCTIONS.get(feature_mode, '')}"
            ),
            max_output_tokens=output_token_limit,
            # Avoid the SDK's default 5 attempts (with 1/2/4/8s waits) on
            # overload; the app immediately tries its configured fallback model.
            http_options=types.HttpOptions(
                timeout=15000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ),
    )
    return (response.text or "").strip()


def _is_temporary_unavailable(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    detail = str(exc).upper()
    class_name = type(exc).__name__.lower()
    return (
        code in (408, 503, 504)
        or "503" in detail
        or "504" in detail
        or "UNAVAILABLE" in detail
        or "timeout" in class_name
        or "timed out" in detail
    )


def _gemini_failure_message(exc: Exception, fallback_attempted: bool = False) -> str:
    detail = str(exc).upper()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429 or "429" in detail or "RESOURCE_EXHAUSTED" in detail or "QUOTA_EXCEEDED" in detail:
        return "Gemini rate limit or quota exceeded (429). Check your project's live limits in Google AI Studio, then retry after the listed reset or cooldown."
    if code in (401, 403) or "UNAUTHENTICATED" in detail or "PERMISSION_DENIED" in detail:
        return "Gemini rejected this request (authentication or project access). Check the API key, enabled model, and project billing."
    if code == 404 or "NOT_FOUND" in detail:
        return f"Gemini returned 404 for '{settings.GEMINI_MODEL}'. The model ID may be valid but unavailable to this API project; verify model access and that the key belongs to the intended project."
    if code == 503 or "UNAVAILABLE" in detail:
        if fallback_attempted:
            return "Gemini's primary and backup models are temporarily at capacity (503). Try again shortly."
        return "Gemini is temporarily at capacity (503). Try again shortly."
    if code in (408, 504) or "TIMEOUT" in detail or "TIMED OUT" in detail or "TIMEOUT" in type(exc).__name__.upper():
        if fallback_attempted:
            return "Gemini's primary and backup requests both timed out. A local answer could not be generated for this research feature; try again shortly."
        return "Gemini took too long to answer. The app tried its backup model, but it also timed out. Try a shorter or narrower question."
    return "Gemini couldn't generate this result. Check the Django server terminal for the detailed error, then retry."


def _extractive_answer(question: str, results: list[RetrievedChunk]) -> str:
    query_terms = _meaningful_terms(question)
    if not query_terms and _is_overview_question(question) and results:
        # For overview prompts, return the beginning of the best retrieved
        # passage so the local fallback remains useful without inventing facts.
        passage = results[0].chunk.text.strip()
        return f"{passage[:700].rsplit(' ', 1)[0]} [S1]" if passage else ""
    candidates: list[tuple[int, float, str]] = []
    for source_index, result in enumerate(results, start=1):
        sentences = re.split(r"(?<=[.!?])\s+|\n+", result.chunk.text)
        for sentence in sentences:
            sentence = sentence.strip()
            overlap = len(query_terms & _meaningful_terms(sentence))
            if overlap and len(sentence) >= 25:
                candidates.append((overlap, result.score, f"{sentence} [S{source_index}]"))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected, seen = [], set()
    for _, _, sentence in candidates:
        key = sentence.lower()
        if key not in seen:
            selected.append(sentence)
            seen.add(key)
        if len(selected) == 3:
            break
    return " ".join(selected)


def _meaningful_terms(text: str) -> set[str]:
    terms = {term for term in re.findall(r"[a-z0-9]{2,}", text.lower()) if term not in STOP_WORDS}
    return {term[:-1] if len(term) > 4 and term.endswith("s") else term for term in terms}


def _is_overview_question(question: str) -> bool:
    raw_terms = set(re.findall(r"[a-z0-9]{2,}", question.lower()))
    return bool(raw_terms & OVERVIEW_TERMS)


def _is_synthesis_request(question: str) -> bool:
    raw_terms = set(re.findall(r"[a-z0-9]{2,}", question.lower()))
    return bool(raw_terms & SYNTHESIS_TERMS)


def _serialize_source(result: RetrievedChunk, source_id: int) -> dict:
    chunk = result.chunk
    location = f"Page {chunk.page_number}" if chunk.page_number else f"Passage {chunk.chunk_index + 1}"
    return {
        "id": source_id,
        "document_id": chunk.document_id,
        "document": chunk.document.name,
        "location": location,
        "page_number": chunk.page_number,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "score": round(max(0.0, result.score), 3),
    }


def _not_found_payload() -> dict:
    return {"answer": NOT_FOUND_MESSAGE, "found": False, "sources": [], "provider": "Retrieval gate"}

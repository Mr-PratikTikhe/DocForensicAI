"""Document-grounded structured learning tools."""
import json
import logging

from django.conf import settings

from assistant.models import Document

from .rag import _is_temporary_unavailable, retrieve

logger = logging.getLogger(__name__)

QUIZ_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "questions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "question": {"type": "STRING"},
                    "options": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "correct_answer": {"type": "INTEGER"},
                    "explanation": {"type": "STRING"},
                    "source_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                },
                "required": ["question", "options", "correct_answer", "explanation", "source_ids"],
            },
        },
    },
    "required": ["title", "questions"],
}


class QuizGenerationError(Exception):
    pass


def generate_quiz(document_ids: list[int], question_count: int, difficulty: str, topic: str = "") -> dict:
    documents = list(Document.objects.filter(id__in=document_ids, status=Document.Status.READY))
    if len(documents) != len(set(document_ids)):
        raise QuizGenerationError("Select at least one indexed document and try again.")

    search_text = topic.strip() or "key concepts definitions principles findings methods and conclusions"
    results = retrieve(search_text, document_ids, limit=8, feature_mode="quiz", retrieval_query_override=search_text)
    if not results:
        raise QuizGenerationError("No searchable passages were found in the selected documents.")

    sources = []
    for index, result in enumerate(results, start=1):
        chunk = result.chunk
        sources.append({
            "id": index,
            "document_id": chunk.document_id,
            "document": chunk.document.name,
            "location": f"Page {chunk.page_number}" if chunk.page_number else f"Passage {chunk.chunk_index + 1}",
            "page_number": chunk.page_number or 1,
            "text": chunk.text[:2200],
        })
    try:
        generated = _generate_quiz_json(sources, question_count, difficulty, topic)
        return validate_quiz(generated, sources, question_count)
    except QuizGenerationError:
        raise
    except Exception as exc:
        logger.exception("Grounded quiz generation failed")
        if _is_temporary_unavailable(exc):
            raise QuizGenerationError("Gemini is temporarily unavailable. Please retry the quiz in a moment.") from exc
        raise QuizGenerationError("The quiz could not be generated from these passages. Please retry.") from exc


def _generate_quiz_json(sources: list[dict], question_count: int, difficulty: str, topic: str) -> dict:
    if not settings.GEMINI_API_KEY:
        raise QuizGenerationError("A Gemini API key is required to generate a grounded quiz.")
    from google import genai
    from google.genai import types

    context = "\n\n".join(f"[S{source['id']}] {source['document']} · {source['location']}\n{source['text']}" for source in sources)
    prompt = (
        f"Create exactly {question_count} {difficulty} multiple choice questions. Topic focus: {topic or 'key concepts in the provided passages'}. "
        "Use only information explicit in the passages. Each item must have exactly four distinct plausible options, one correct option, "
        "a brief evidence-based explanation, and source_ids containing one or more supplied source numbers. Do not use outside facts. "
        "If there is not enough evidence for a question, do not invent one.\n\nPASSAGES:\n" + context
    )
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    models = [settings.GEMINI_MODEL]
    fallback = getattr(settings, "GEMINI_FALLBACK_MODEL", "")
    if fallback and fallback not in models:
        models.append(fallback)
    last_error = None
    for model in models:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=QUIZ_SCHEMA,
                    max_output_tokens=1800,
                    http_options=types.HttpOptions(timeout=18000, retry_options=types.HttpRetryOptions(attempts=1)),
                ),
            )
            return json.loads(response.text or "")
        except Exception as exc:
            last_error = exc
            if not _is_temporary_unavailable(exc):
                break
            logger.warning("Quiz generation model %s unavailable; trying fallback", model)
    raise last_error or QuizGenerationError("Quiz generation failed.")


def validate_quiz(payload: dict, sources: list[dict], question_count: int) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise QuizGenerationError("Gemini returned an invalid quiz format. Please retry.")
    if len(payload["questions"]) != question_count:
        raise QuizGenerationError("Gemini did not return the requested number of questions. Please retry.")
    source_map = {source["id"]: source for source in sources}
    questions = []
    for item in payload["questions"]:
        if not isinstance(item, dict):
            raise QuizGenerationError("Gemini returned an invalid question. Please retry.")
        question = str(item.get("question", "")).strip()
        options = item.get("options")
        correct = item.get("correct_answer")
        explanation = str(item.get("explanation", "")).strip()
        try:
            source_ids = list(dict.fromkeys(int(value) for value in item.get("source_ids", [])))
        except (TypeError, ValueError):
            source_ids = []
        if (not question or not isinstance(options, list) or len(options) != 4
                or any(not isinstance(option, str) or not option.strip() for option in options)
                or len({option.strip().casefold() for option in options}) != 4
                or isinstance(correct, bool) or not isinstance(correct, int) or correct not in range(4)
                or not explanation or not source_ids or any(source_id not in source_map for source_id in source_ids)):
            raise QuizGenerationError("A generated question failed its evidence or answer checks. Please retry.")
        questions.append({
            "question": question,
            "options": [option.strip() for option in options],
            "correct_answer": correct,
            "explanation": explanation,
            "citations": [{key: source_map[source_id][key] for key in ("document_id", "document", "location", "page_number")} for source_id in source_ids],
        })
    return {"title": str(payload.get("title", "Document quiz")).strip()[:120] or "Document quiz", "questions": questions}

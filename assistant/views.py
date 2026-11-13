import hmac
import json
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import DemoDailyUsage, Document
from .services.processing import process_document
from .services.rag import answer_question
from .services.learning import QuizGenerationError, generate_quiz


FEATURE_MODES = {
    "compare", "quiz", "flashcards", "explain", "literature_review", "research_gaps", "knowledge_map",
}


@require_http_methods(["GET", "POST"])
def site_login(request):
    if not settings.SITE_ACCESS_PASSWORD:
        return render(request, "assistant/login.html", {"error": "Beta access is not configured."}, status=503)
    if request.method == "POST":
        submitted = request.POST.get("password", "")
        if hmac.compare_digest(submitted, settings.SITE_ACCESS_PASSWORD):
            from .middleware import current_password_fingerprint
            request.session["site_password_fingerprint"] = current_password_fingerprint()
            next_url = request.POST.get("next", "")
            from django.utils.http import url_has_allowed_host_and_scheme
            if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                return redirect(next_url)
            return redirect("assistant:dashboard")
        return render(
            request,
            "assistant/login.html",
            {"error": "That password did not match.", "next": request.POST.get("next", "")},
            status=401,
        )
    return render(request, "assistant/login.html", {"next": request.GET.get("next", "")})


@require_POST
def site_logout(request):
    request.session.pop("site_password_fingerprint", None)
    return redirect("assistant:site_login")


@require_GET
def dashboard(request):
    return render(request, "assistant/index.html", {"gemini_enabled": bool(settings.GEMINI_API_KEY), "demo_mode": False})


@require_GET
def demo_dashboard(request):
    return render(request, "assistant/index.html", {"gemini_enabled": bool(settings.GEMINI_API_KEY), "demo_mode": True})


@require_GET
def documents_api(request):
    return JsonResponse({"documents": [_serialize_document(document) for document in Document.objects.all()]})


@require_GET
def demo_documents_api(request):
    documents = Document.objects.filter(is_demo=True, status=Document.Status.READY)
    return JsonResponse({"documents": [_serialize_document(document, demo=True) for document in documents]})


@require_GET
def document_download(request, document_id):
    document = get_object_or_404(Document, pk=document_id)
    document.file.open("rb")
    content_type = "application/pdf" if document.file_type == "pdf" else "text/plain; charset=utf-8"
    response = FileResponse(document.file, as_attachment=False, filename=document.name, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def demo_document_download(request, document_id):
    document = get_object_or_404(Document, pk=document_id, is_demo=True, status=Document.Status.READY)
    document.file.open("rb")
    content_type = "application/pdf" if document.file_type == "pdf" else "text/plain; charset=utf-8"
    response = FileResponse(document.file, as_attachment=False, filename=document.name, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "public, max-age=300"
    return response


@require_POST
def upload_document(request):
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"error": "Choose a PDF or TXT file to upload."}, status=400)

    extension = Path(upload.name).suffix.lower().lstrip(".")
    if extension not in {"pdf", "txt"}:
        return JsonResponse({"error": "Only PDF and TXT files are supported."}, status=400)
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if upload.size > max_bytes:
        return JsonResponse({"error": f"Files must be {settings.MAX_UPLOAD_SIZE_MB} MB or smaller."}, status=400)

    document = Document.objects.create(
        name=Path(upload.name).name[:255], file=upload, file_type=extension, file_size=upload.size
    )
    try:
        process_document(document)
    except Exception as exc:
        return JsonResponse({"error": str(exc), "document": _serialize_document(document)}, status=422)
    return JsonResponse({"document": _serialize_document(document)}, status=201)


@require_http_methods(["DELETE"])
def delete_document(request, document_id):
    document = get_object_or_404(Document, pk=document_id)
    stored_file = document.file
    document.delete()
    if stored_file:
        stored_file.delete(save=False)
    return JsonResponse({"deleted": True})


@require_GET
def document_chunks(request, document_id):
    document = get_object_or_404(Document, pk=document_id)
    chunks = document.chunks.order_by("chunk_index")
    return JsonResponse({
        "document": _serialize_document(document),
        "chunks": [
            {
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "page_number": chunk.page_number or 1,
                "text": chunk.text,
            }
            for chunk in chunks
        ],
    })


@require_GET
def demo_document_chunks(request, document_id):
    document = get_object_or_404(Document, pk=document_id, is_demo=True, status=Document.Status.READY)
    return JsonResponse({
        "document": _serialize_document(document, demo=True),
        "chunks": [
            {"id": chunk.id, "chunk_index": chunk.chunk_index, "page_number": chunk.page_number or 1, "text": chunk.text}
            for chunk in document.chunks.order_by("chunk_index")
        ],
    })


@require_POST
def ask_question(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON request."}, status=400)

    question = str(payload.get("question", "")).strip()
    if len(question) < 3:
        return JsonResponse({"error": "Enter a complete question."}, status=400)
    if len(question) > 2000:
        return JsonResponse({"error": "Questions must be 2,000 characters or fewer."}, status=400)

    raw_ids = payload.get("document_ids") or []
    if not isinstance(raw_ids, list):
        return JsonResponse({"error": "document_ids must be a list."}, status=400)
    try:
        document_ids = [int(value) for value in raw_ids]
    except (TypeError, ValueError):
        return JsonResponse({"error": "Each document ID must be a number."}, status=400)

    try:
        feature_mode = payload.get("feature_mode")
        if feature_mode is not None and (not isinstance(feature_mode, str) or feature_mode not in FEATURE_MODES):
            return JsonResponse({"error": "Unknown research feature."}, status=400)
        result = answer_question(question, document_ids or None, feature_mode=feature_mode)
    except RuntimeError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    return JsonResponse(result)


@require_POST
def demo_ask_question(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON request."}, status=400)

    question = str(payload.get("question", "")).strip()
    if len(question) < 3:
        return JsonResponse({"error": "Enter a complete question."}, status=400)
    if len(question) > 500:
        return JsonResponse({"error": "Demo questions must be 500 characters or fewer."}, status=400)

    raw_ids = payload.get("document_ids") or []
    if not isinstance(raw_ids, list):
        return JsonResponse({"error": "document_ids must be a list."}, status=400)
    try:
        document_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Each document ID must be a number."}, status=400)
    if not document_ids:
        return JsonResponse({"error": "Select at least one demo document."}, status=400)

    demo_documents = list(Document.objects.filter(
        is_demo=True, status=Document.Status.READY, id__in=document_ids
    ).order_by("id"))
    if len(demo_documents) != len(document_ids):
        return JsonResponse({"error": "Choose only ready documents from the public demo."}, status=400)
    if not demo_documents:
        return JsonResponse({"error": "The public demo is being prepared. Please check back soon."}, status=503)

    allowed, remaining = DemoDailyUsage.reserve_question()
    if not allowed:
        return JsonResponse({"error": "The public demo has reached today's shared question limit. Please try again tomorrow."}, status=429)

    try:
        result = answer_question(question, [document.id for document in demo_documents], feature_mode=None)
    except RuntimeError as exc:
        return JsonResponse({"error": str(exc), "remaining_today": remaining}, status=503)
    result["demo_remaining_today"] = remaining
    return JsonResponse(result)


@require_POST
def quiz_api(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON request."}, status=400)
    raw_ids = payload.get("document_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return JsonResponse({"error": "Select at least one document for the quiz."}, status=400)
    try:
        document_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Each document ID must be a number."}, status=400)
    if any(value <= 0 for value in document_ids):
        return JsonResponse({"error": "Document IDs must be positive numbers."}, status=400)
    try:
        question_count = int(payload.get("question_count", 5))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Choose between 3 and 10 questions."}, status=400)
    if question_count not in {3, 5, 8, 10}:
        return JsonResponse({"error": "Choose between 3, 5, 8, or 10 questions."}, status=400)
    difficulty = payload.get("difficulty", "medium")
    if difficulty not in {"easy", "medium", "hard"}:
        return JsonResponse({"error": "Choose easy, medium, or hard difficulty."}, status=400)
    topic = str(payload.get("topic", "")).strip()
    if len(topic) > 250:
        return JsonResponse({"error": "Quiz topics must be 250 characters or fewer."}, status=400)
    try:
        return JsonResponse(generate_quiz(document_ids, question_count, difficulty, topic))
    except QuizGenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=503)


def _serialize_document(document: Document, demo: bool = False) -> dict:
    return {
        "id": document.id,
        "name": document.name,
        "file_url": f"/api/{'demo/' if demo else ''}documents/{document.id}/download/" if document.file else None,
        "file_type": document.file_type.upper(),
        "file_size": document.file_size,
        "status": document.status,
        "page_count": document.page_count,
        "chunk_count": document.chunk_count,
        "error": document.error_message,
        "created_at": document.created_at.isoformat(),
        "created_date": document.created_at.strftime("%Y-%m-%d"),
    }

import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Document, DocumentChunk
from .services.embeddings import local_embedding
from .services.extraction import TextSection, chunk_sections
from .services.rag import NOT_FOUND_MESSAGE, answer_question
from .services.learning import QuizGenerationError, validate_quiz


class ChunkingTests(TestCase):
    def test_chunks_keep_overlap_and_page_number(self):
        words = [f"word{index}" for index in range(20)]
        chunks = chunk_sections([TextSection(" ".join(words), 3)], chunk_size=10, overlap=2)

        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].text.split()[-2:], chunks[1].text.split()[:2])
        self.assertTrue(all(chunk.page_number == 3 for chunk in chunks))


class RagTests(TestCase):
    def setUp(self):
        self.document = Document.objects.create(
            name="science.txt", file="documents/science.txt", file_type="txt",
            file_size=100, status=Document.Status.READY, page_count=1, chunk_count=1,
        )
        text = (
            "Photosynthesis converts light energy into chemical energy in plants. "
            "Chlorophyll absorbs light, while carbon dioxide and water help produce glucose and oxygen."
        )
        DocumentChunk.objects.create(
            document=self.document, chunk_index=0, text=text,
            embedding=local_embedding(text), embedding_provider="local-hash",
        )

    @override_settings(GEMINI_API_KEY="")
    def test_relevant_question_returns_grounded_source(self):
        result = answer_question("What does chlorophyll absorb?", [self.document.id])

        self.assertTrue(result["found"])
        self.assertIn("Chlorophyll", result["answer"])
        self.assertEqual(result["sources"][0]["document"], "science.txt")

    @override_settings(GEMINI_API_KEY="")
    def test_unrelated_question_is_rejected(self):
        result = answer_question("Who won the lunar chess championship?", [self.document.id])

        self.assertFalse(result["found"])
        self.assertEqual(result["answer"], NOT_FOUND_MESSAGE)
        self.assertEqual(result["sources"], [])


class ApiTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root, GEMINI_API_KEY="")
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_txt_upload_is_processed_and_indexed(self):
        upload = SimpleUploadedFile(
            "guide.txt",
            b"Django uses URL patterns to route requests to view functions. " * 8,
            content_type="text/plain",
        )
        response = self.client.post(reverse("assistant:upload_document"), {"file": upload})

        self.assertEqual(response.status_code, 201)
        document = Document.objects.get()
        self.assertEqual(document.status, Document.Status.READY)
        self.assertGreater(document.chunk_count, 0)
        self.assertTrue(document.chunks.first().embedding)

    def test_upload_rejects_unsupported_extension(self):
        upload = SimpleUploadedFile("payload.html", b"<h1>unsafe</h1>", content_type="text/html")
        response = self.client.post(reverse("assistant:upload_document"), {"file": upload})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Document.objects.count(), 0)

    def test_ask_requires_a_question(self):
        response = self.client.post(
            reverse("assistant:ask_question"), data='{"question":""}', content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_quiz_rejects_missing_document_selection(self):
        response = self.client.post(reverse("assistant:quiz_api"), data='{"document_ids":[]}', content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_quiz_rejects_unsupported_question_count(self):
        response = self.client.post(
            reverse("assistant:quiz_api"),
            data='{"document_ids":[1],"question_count":4}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class QuizValidationTests(TestCase):
    def setUp(self):
        self.sources = [{"id": 1, "document_id": 11, "document": "science.pdf", "location": "Page 2", "page_number": 2, "text": "Source passage"}]

    def test_valid_quiz_maps_source_ids_to_citations(self):
        quiz = validate_quiz({
            "title": "Science",
            "questions": [{"question": "What is stated?", "options": ["A", "B", "C", "D"], "correct_answer": 0,
                            "explanation": "The passage says so.", "source_ids": [1]}],
        }, self.sources, 1)
        self.assertEqual(quiz["questions"][0]["citations"][0]["document"], "science.pdf")
        self.assertEqual(quiz["questions"][0]["citations"][0]["document_id"], 11)

    def test_invalid_source_reference_is_rejected(self):
        with self.assertRaises(QuizGenerationError):
            validate_quiz({
                "questions": [{"question": "Q?", "options": ["A", "B", "C", "D"], "correct_answer": 0,
                                "explanation": "Evidence.", "source_ids": [9]}],
            }, self.sources, 1)

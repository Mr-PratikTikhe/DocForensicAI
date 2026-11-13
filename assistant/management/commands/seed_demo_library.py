from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management import BaseCommand, call_command
from django.db import transaction

from assistant.models import Document, DocumentChunk
from assistant.services.embeddings import local_embedding
from assistant.services.extraction import chunk_sections, extract_sections


SOURCES = (
    (
        "The Origin and Development of the Quantum Theory — Max Planck.pdf",
        "planck-origin-quantum-theory.pdf",
    ),
    (
        "Relativity: The Special and General Theory — Albert Einstein.pdf",
        "einstein-relativity.pdf",
    ),
)


class Command(BaseCommand):
    help = "Seed the public demo with its synthetic report and sample physics PDFs."

    def handle(self, *args, **options):
        call_command("seed_demo_document")
        source_dir = Path(__file__).resolve().parents[3] / "demo_sources"
        for display_name, filename in SOURCES:
            document, created = Document.objects.get_or_create(
                name=display_name,
                is_demo=True,
                defaults={"file_type": "pdf", "status": Document.Status.PROCESSING},
            )
            if not created and document.status == Document.Status.READY and document.chunk_count:
                self.stdout.write(self.style.SUCCESS(f"Already ready: {display_name}"))
                continue

            source_path = source_dir / filename
            if not source_path.is_file():
                raise FileNotFoundError(f"Demo source PDF is missing: {source_path}")
            encoded = source_path.read_bytes()
            if len(encoded) > 4 * 1024 * 1024:
                raise ValueError(f"{filename} exceeds the 4 MB demo size limit.")

            sections = extract_sections(source_path, "pdf")
            chunks = chunk_sections(sections)
            if not chunks:
                raise ValueError(f"No readable passages found in {filename}.")

            document.file_type = "pdf"
            document.file_size = len(encoded)
            document.status = Document.Status.PROCESSING
            document.error_message = ""
            document.save()
            document.file.save(filename, ContentFile(encoded), save=True)

            with transaction.atomic():
                DocumentChunk.objects.filter(document=document).delete()
                DocumentChunk.objects.bulk_create([
                    DocumentChunk(
                        document=document,
                        chunk_index=index,
                        page_number=chunk.page_number,
                        text=chunk.text,
                        embedding=local_embedding(chunk.text),
                        embedding_provider="local-hash",
                    )
                    for index, chunk in enumerate(chunks)
                ])
                document.page_count = len(sections)
                document.chunk_count = len(chunks)
                document.status = Document.Status.READY
                document.save(update_fields=["page_count", "chunk_count", "status", "error_message"])

            self.stdout.write(self.style.SUCCESS(
                f"Added {display_name}: {len(sections)} pages, {len(chunks)} passages, {len(encoded)} bytes."
            ))

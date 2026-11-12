from django.db import transaction

from assistant.models import Document, DocumentChunk

from .embeddings import embed_documents
from .extraction import chunk_sections, extract_sections


def process_document(document: Document) -> Document:
    try:
        sections = extract_sections(document.file.path, document.file_type)
        chunks = chunk_sections(sections)
        if not chunks:
            raise ValueError("The document did not contain enough readable text to index.")
        vectors, provider = embed_documents([chunk.text for chunk in chunks])

        with transaction.atomic():
            DocumentChunk.objects.bulk_create(
                [
                    DocumentChunk(
                        document=document,
                        chunk_index=index,
                        page_number=chunk.page_number,
                        text=chunk.text,
                        embedding=vectors[index],
                        embedding_provider=provider,
                    )
                    for index, chunk in enumerate(chunks)
                ]
            )
            document.page_count = len(sections) if document.file_type == "pdf" else 1
            document.chunk_count = len(chunks)
            document.status = Document.Status.READY
            document.error_message = ""
            document.save(update_fields=["page_count", "chunk_count", "status", "error_message"])
    except Exception as exc:
        document.status = Document.Status.FAILED
        document.error_message = str(exc)[:1000]
        document.save(update_fields=["status", "error_message"])
        raise
    return document

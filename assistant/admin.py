from django.contrib import admin

from .models import Document, DocumentChunk


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "file_type", "page_count", "chunk_count", "created_at")
    list_filter = ("status", "file_type")
    search_fields = ("name",)


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "chunk_index", "page_number", "embedding_provider")
    search_fields = ("document__name", "text")

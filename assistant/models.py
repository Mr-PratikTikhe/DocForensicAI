from django.db import models


class Document(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    name = models.CharField(max_length=255)
    file = models.FileField(upload_to="documents/%Y/%m/")
    file_type = models.CharField(max_length=8)
    file_size = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROCESSING)
    page_count = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class DocumentChunk(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    chunk_index = models.PositiveIntegerField()
    page_number = models.PositiveIntegerField(null=True, blank=True)
    text = models.TextField()
    embedding = models.JSONField(default=list)
    embedding_provider = models.CharField(max_length=32, default="local-hash")

    class Meta:
        ordering = ["document_id", "chunk_index"]
        constraints = [
            models.UniqueConstraint(fields=["document", "chunk_index"], name="unique_document_chunk")
        ]

    def __str__(self):
        return f"{self.document.name} · chunk {self.chunk_index + 1}"

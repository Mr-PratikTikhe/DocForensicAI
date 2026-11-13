from django.db import models
from django.utils import timezone


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
    is_demo = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class DemoDailyUsage(models.Model):
    day = models.DateField(unique=True)
    question_count = models.PositiveSmallIntegerField(default=0)

    @classmethod
    def reserve_question(cls, daily_limit=20):
        from django.db import transaction

        with transaction.atomic():
            usage, _ = cls.objects.select_for_update().get_or_create(day=timezone.localdate())
            if usage.question_count >= daily_limit:
                return False, 0
            usage.question_count += 1
            usage.save(update_fields=["question_count"])
            return True, daily_limit - usage.question_count


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

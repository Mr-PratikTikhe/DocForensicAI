from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Document",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("file", models.FileField(upload_to="documents/%Y/%m/")),
                ("file_type", models.CharField(max_length=8)),
                ("file_size", models.PositiveBigIntegerField(default=0)),
                ("status", models.CharField(choices=[("processing", "Processing"), ("ready", "Ready"), ("failed", "Failed")], default="processing", max_length=16)),
                ("page_count", models.PositiveIntegerField(default=0)),
                ("chunk_count", models.PositiveIntegerField(default=0)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="DocumentChunk",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chunk_index", models.PositiveIntegerField()),
                ("page_number", models.PositiveIntegerField(blank=True, null=True)),
                ("text", models.TextField()),
                ("embedding", models.JSONField(default=list)),
                ("embedding_provider", models.CharField(default="local-hash", max_length=32)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chunks", to="assistant.document")),
            ],
            options={"ordering": ["document_id", "chunk_index"]},
        ),
        migrations.AddConstraint(
            model_name="documentchunk",
            constraint=models.UniqueConstraint(fields=("document", "chunk_index"), name="unique_document_chunk"),
        ),
    ]

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction

from assistant.models import Document, DocumentChunk
from assistant.services.embeddings import local_embedding
from assistant.services.extraction import TextSection, chunk_sections


DEMO_REPORT = """DOCFORENSIC AI · PUBLIC DEMO REPORT
ORION MICROGRID PILOT — SYNTHETIC CASE STUDY

This fictional report is sample material created only to demonstrate DocForensic AI. The sites, figures, and findings below are synthetic and do not describe a real organization or project.

PILOT DESIGN
The fictional Orion pilot operated for twelve months across three community clinics. Each site received a 180 kW rooftop solar array and a battery system with 240 kWh of storage. The evaluation team reviewed monthly utility records, equipment logs, and interviews with clinic staff. There was no comparison group and no independent audit of the equipment logs.

OBSERVED OUTCOMES
Across the three sites, recorded grid-outage time averaged six hours per clinic per month during the twelve-month baseline period. During the pilot, the recorded average was 2.5 hours per clinic per month. This is a reduction of about 58 percent, based on the reported averages. The largest changes occurred at the two clinics with fewer cloudy-season interruptions. The report does not provide enough site-level measurements to establish why the changes differed.

LIMITATIONS AND NEXT STEPS
The small sample of three clinics, lack of a comparison group, and reliance on operational logs limit conclusions about whether the solar and battery equipment caused the reduction. The report does not calculate lifecycle cost, emissions avoided, or long-term battery replacement needs. A follow-up evaluation should include more sites, independently checked outage records, seasonal cost data, and a comparison group.

END OF SYNTHETIC DEMO REPORT
"""


class Command(BaseCommand):
    help = "Create or refresh the synthetic document used by the public read-only demo."

    def handle(self, *args, **options):
        document = Document.objects.filter(is_demo=True).order_by("id").first()
        if document and document.status == Document.Status.READY and document.chunk_count:
            self.stdout.write(self.style.SUCCESS(f"Public demo document is ready (id={document.pk})."))
            return

        if document is None:
            document = Document(is_demo=True, name="Orion Microgrid Pilot — Synthetic Demo Report.txt", file_type="txt")
        else:
            document.chunks.all().delete()

        encoded = DEMO_REPORT.encode("utf-8")
        document.name = "Orion Microgrid Pilot — Synthetic Demo Report.txt"
        document.file_type = "txt"
        document.file_size = len(encoded)
        document.status = Document.Status.PROCESSING
        document.error_message = ""
        document.save()
        document.file.save("orion-microgrid-demo.txt", ContentFile(encoded), save=True)

        sections = chunk_sections([TextSection(text=DEMO_REPORT, page_number=1)])
        with transaction.atomic():
            DocumentChunk.objects.bulk_create([
                DocumentChunk(
                    document=document,
                    chunk_index=index,
                    page_number=section.page_number,
                    text=section.text,
                    embedding=local_embedding(section.text),
                    embedding_provider="local-hash",
                )
                for index, section in enumerate(sections)
            ])
            document.page_count = 1
            document.chunk_count = len(sections)
            document.status = Document.Status.READY
            document.save(update_fields=["page_count", "chunk_count", "status", "error_message"])

        self.stdout.write(self.style.SUCCESS(f"Created public demo document (id={document.pk})."))

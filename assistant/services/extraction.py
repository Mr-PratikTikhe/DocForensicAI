import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass(frozen=True)
class TextSection:
    text: str
    page_number: int | None


def extract_sections(file_path: str, file_type: str) -> list[TextSection]:
    if file_type == "pdf":
        reader = PdfReader(file_path)
        sections = [
            TextSection(text=_clean_text(page.extract_text() or ""), page_number=index)
            for index, page in enumerate(reader.pages, start=1)
        ]
    elif file_type == "txt":
        raw = Path(file_path).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        sections = [TextSection(text=_clean_text(text), page_number=None)]
    else:
        raise ValueError("Only PDF and TXT documents are supported.")

    if not any(section.text.strip() for section in sections):
        raise ValueError("No readable text was found. Scanned PDFs require OCR before upload.")
    return sections


def chunk_sections(
    sections: list[TextSection], chunk_size: int = 180, overlap: int = 35
) -> list[TextSection]:
    if overlap >= chunk_size:
        raise ValueError("Chunk overlap must be smaller than the chunk size.")

    chunks: list[TextSection] = []
    step = chunk_size - overlap
    for section in sections:
        words = section.text.split()
        for start in range(0, len(words), step):
            part = words[start : start + chunk_size]
            if not part:
                continue
            chunks.append(TextSection(text=" ".join(part), page_number=section.page_number))
            if start + chunk_size >= len(words):
                break
    return chunks


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

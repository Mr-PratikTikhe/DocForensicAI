import hashlib
import logging
import math
import re
from collections import Counter

from django.conf import settings


LOCAL_DIMENSIONS = 512
TOKEN_PATTERN = re.compile(r"[a-z0-9]{2,}")
logger = logging.getLogger(__name__)


def local_embedding(text: str, dimensions: int = LOCAL_DIMENSIONS) -> list[float]:
    tokens = TOKEN_PATTERN.findall(text.lower())
    features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    counts = Counter(features)
    vector = [0.0] * dimensions
    for feature, count in counts.items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        number = int.from_bytes(digest, "big")
        index = number % dimensions
        sign = 1.0 if number & 1 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    return normalize(vector)


def embed_documents(texts: list[str]) -> tuple[list[list[float]], str]:
    if not settings.GEMINI_API_KEY:
        return [local_embedding(text) for text in texts], "local-hash"

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 50):
            response = client.models.embed_content(
                model=settings.GEMINI_EMBEDDING_MODEL,
                contents=texts[start : start + 50],
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=768,
                    http_options=types.HttpOptions(
                        timeout=5000,
                        retry_options=types.HttpRetryOptions(attempts=1),
                    )
                ),
            )
            vectors.extend(normalize(item.values) for item in response.embeddings)
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding API returned an unexpected number of vectors.")
        return vectors, f"gemini:{settings.GEMINI_EMBEDDING_MODEL}"
    except Exception as exc:
        logger.warning(
            "Gemini document embedding unavailable (%s); using local embeddings for this document",
            type(exc).__name__,
        )
        return [local_embedding(text) for text in texts], "local-hash"


def embed_query(text: str, provider: str) -> list[float]:
    if provider == "local-hash":
        return local_embedding(text)
    if not provider.startswith("gemini:") or not settings.GEMINI_API_KEY:
        raise RuntimeError("The embedding provider used for this document is unavailable.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.embed_content(
        model=provider.split(":", 1)[1],
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=768,
            http_options=types.HttpOptions(
                timeout=5000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ),
    )
    return normalize(response.embeddings[0].values)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def normalize(vector) -> list[float]:
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    return values if not norm else [value / norm for value in values]

"""Stage 3 - Select (retrieval).

The brief warns the document set may exceed 100 pages and contain redundant or
irrelevant material. Sending all of it to the model for every dimension would
be expensive, slow, and worse: long-context dilution measurably degrades
judgement quality, and it destroys traceability because you can no longer say
which passage drove which conclusion.

So each risk dimension retrieves its own small slice of the corpus. Two
strategies are provided behind one function signature:

  keyword   (default) -- IDF-weighted term matching against the dimension's
              vocabulary. Zero cost, zero latency, fully deterministic, and
              entirely inspectable: you can read the score and see why a chunk
              was chosen. Good enough because the dimension vocabularies are
              domain-specific jargon, which is exactly where lexical matching
              is strong.

  embedding -- cosine similarity over OpenAI embeddings. Better at synonyms
              and paraphrase ("we outsource our core banking to a third party"
              vs "vendor concentration"). Costs an API call per chunk and makes
              the run non-reproducible without a cache.

Default is keyword because it is defensible, free and deterministic. The
embedding path exists to show the upgrade is a config flag, not a rewrite.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from ..models import Chunk

_TOKEN = re.compile(r"[a-z][a-z0-9\-']+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _idf(chunks: list[Chunk]) -> dict[str, float]:
    n = max(len(chunks), 1)
    df: Counter[str] = Counter()
    for chunk in chunks:
        df.update(set(_tokens(chunk.text)))
    return {term: math.log(1 + n / (1 + count)) for term, count in df.items()}


def _keyword_scores(chunks: list[Chunk], keywords: list[str], idf: dict[str, float]) -> list[float]:
    phrases = [k.lower() for k in keywords]
    scores: list[float] = []
    for chunk in chunks:
        lowered = chunk.text.lower()
        toks = _tokens(chunk.text)
        length_norm = math.sqrt(max(len(toks), 1))
        score = 0.0
        for phrase in phrases:
            hits = lowered.count(phrase)
            if not hits:
                continue
            # Weight a phrase by the rarity of its rarest word: "capital" is
            # common, "ICAAP" is not, and the latter is far more diagnostic.
            parts = _tokens(phrase) or [phrase]
            weight = max((idf.get(p, 1.0) for p in parts), default=1.0)
            # Multi-word phrases are stronger signals than bare terms.
            weight *= 1.0 + 0.5 * (len(parts) - 1)
            score += weight * math.log(1 + hits)
        scores.append(score / length_norm * 10)
    return scores


def _embedding_scores(chunks: list[Chunk], query: str, settings, logger) -> list[float]:  # pragma: no cover
    import numpy as np
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, timeout=settings.request_timeout)
    texts = [c.text for c in chunks] + [query]
    resp = client.embeddings.create(model=settings.embedding_model, input=texts)
    vectors = np.array([d.embedding for d in resp.data], dtype="float32")
    matrix, q = vectors[:-1], vectors[-1]
    matrix /= (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    q = q / (np.linalg.norm(q) + 1e-9)
    logger.log("select.embedded", chunks=len(chunks), model=settings.embedding_model)
    return (matrix @ q).tolist()


def run(chunks: list[Chunk], dimensions: list[dict], settings, logger) -> dict[str, list[Chunk]]:
    """Return {dimension_key: [most relevant chunks, best first]}."""
    if not chunks:
        logger.warn("select.no_chunks")
        return {d["key"]: [] for d in dimensions}

    idf = _idf(chunks)
    selected: dict[str, list[Chunk]] = {}

    for dim in dimensions:
        keywords = dim.get("keywords", [])
        if settings.retrieval == "embedding":
            query = f"{dim['name']}. {dim['description']} {' '.join(keywords)}"
            scores = _embedding_scores(chunks, query, settings, logger)
        else:
            scores = _keyword_scores(chunks, keywords, idf)

        ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)
        top = [c for c, s in ranked[: settings.max_chunks_per_dimension] if s > 0]

        # A dimension with no lexical hits at all is itself information: it
        # usually means the applicant simply did not address the topic. Fall
        # back to the longest few chunks so the model can confirm the absence
        # rather than being handed nothing.
        if not top:
            logger.warn("select.no_match", dimension=dim["key"])
            top = sorted(chunks, key=lambda c: c.char_count, reverse=True)[:3]

        selected[dim["key"]] = top
        logger.log(
            "select.dimension",
            dimension=dim["key"],
            chunks=len(top),
            top_score=round(ranked[0][1], 3) if ranked else 0.0,
            docs=len({c.doc_id for c in top}),
        )

    return selected

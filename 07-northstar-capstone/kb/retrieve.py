from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from langchain_openai import OpenAIEmbeddings

logger = logging.getLogger(__name__)

_KB_DIR = Path(__file__).parent
_EMBEDDING_MODEL = "text-embedding-3-small"

_embeddings_client = OpenAIEmbeddings(model=_EMBEDDING_MODEL)

_kb_cache: dict[str, list[dict]] = {}
_embedding_cache: dict[str, list[list[float]]] = {}


def _load_kb(filename: str) -> list[dict]:
    """Load a knowledge base JSON file from the kb/ directory, caching the result."""
    if filename not in _kb_cache:
        path = _KB_DIR / filename
        with path.open(encoding="utf-8") as f:
            _kb_cache[filename] = json.load(f)
    return _kb_cache[filename]


def _entry_to_chunk_text(entry: dict) -> str:
    """Flatten a KB entry's fields into a single text blob for embedding.
       so a condition’s overview, signs, terminology, etc. all become one string"""
    parts = [str(value) for key, value in entry.items() if key != "id"]
    return " ".join(parts)


def _get_entry_embeddings(filename: str) -> list[list[float]]:
    """Embed every entry in a KB file once per process, caching the vectors. Caches the 
       resulting list of vectors in _embedding_cache[filename], so if you call it again 
       for the same file in the same process, it returns the cached list instead of 
       paying for the API call again. Without that caching, every single query would 
       re-embed all 6 condition entries (or 16 activities) from scratch — wasteful and slow"""
    if filename not in _embedding_cache:
        entries = _load_kb(filename)
        texts = [_entry_to_chunk_text(entry) for entry in entries]
        logger.info(f"Embedding {len(texts)} chunks from {filename}")
        _embedding_cache[filename] = _embeddings_client.embed_documents(texts)
    return _embedding_cache[filename]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


def _retrieve(
    query: str,
    filename: str,
    top_k: int = 3,
    filter_fn=None,
) -> list[dict]:
    """Return the top_k KB entries whose embedding is closest to the query.

    filter_fn, if given, is a function that takes one entry (dict) and returns True/False —
    entries that fail it are excluded BEFORE similarity ranking, not after. That ordering
    matters: filtering first means top_k is always "the k best matches among the ones that
    were even eligible," not "the k best matches overall, which might all happen to be the
    wrong need/category." Added for the Activities KB (see retrieve_from_activities below),
    where we know a hard fact (the required "need": engagement vs. regulation) before doing
    any semantic comparison at all, so there's no reason to let embedding similarity alone
    decide something we can check exactly.
    """
    logger.info(f"Retrieving top {top_k} chunks from {filename} for query: {query!r}")
    entries = _load_kb(filename)
    entry_embeddings = _get_entry_embeddings(filename)

    # zip() pairs each entry with its own pre-computed embedding (same order as _load_kb and
    # _get_entry_embeddings both read the same file the same way). filter_fn is applied here,
    # on the (entry, embedding) pairs together, so a filtered-out entry's embedding is dropped
    # along with it — we never compute similarity for an entry we're about to discard anyway.
    pairs = list(zip(entries, entry_embeddings))
    if filter_fn is not None:
        pairs = [(entry, embedding) for entry, embedding in pairs if filter_fn(entry)]

    query_embedding = _embeddings_client.embed_query(query)
    scored = [(entry, _cosine_similarity(query_embedding, embedding)) for entry, embedding in pairs]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    for entry, score in scored[:top_k]:
        logger.debug(f"Matched {entry.get('id')} with score {score:.4f}")

    return [entry for entry, _ in scored[:top_k]]


def retrieve_from_conditions(query: str, top_k: int = 3) -> list[dict]:
    """Retrieve the most relevant entries from the Conditions + Terminology KB."""
    return _retrieve(query, "conditions.json", top_k=top_k)


def retrieve_from_therapies(query: str, top_k: int = 3) -> list[dict]:
    """Retrieve the most relevant entries from the Therapy KB."""
    return _retrieve(query, "therapies.json", top_k=top_k)


def retrieve_from_activities(query: str, top_k: int = 3, need: str | None = None) -> list[dict]:
    """Retrieve the most relevant entries from the Activities KB.

    need, if given (e.g. "engagement" or "regulation"), restricts candidates to activities.json
    entries whose own "need" field matches exactly, before ranking by similarity — see
    _retrieve()'s filter_fn docstring for why filtering comes first, not after.
    """
    filter_fn = (lambda entry: entry.get("need") == need) if need else None
    return _retrieve(query, "activities.json", top_k=top_k, filter_fn=filter_fn)

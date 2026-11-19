"""Curriculum retrieval for the advisor agent: ingestion, ChromaDB search and a latency benchmark.

Curriculum:
    Module 6 (Document Ingestion and Embeddings): loads the curriculum documents, splits them into
    chunks of 500 tokens with a 50-token overlap and turns them into vectors.
    Module 7 (Vector Stores and Retrieval-Augmented Generation): stores the vectors in ChromaDB and
    retrieves the most similar chunks, with metadata for citations, inside a 1000 ms budget.
    Module 8 (Tools and Function Calling): exposed as the LangChain tool ``search_curriculum``,
    which never raises: a failure returns the structured "Unable to verify" result.

Latency: the embedding model is loaded once by :func:`warm_up`, before the first question. The first
query of a cold process is slow (model loading) and is reported separately by
:func:`benchmark_retrieval`; the budget applies to warmed-up retrieval.
"""

import json
import math
import os
import re
import shutil
import statistics
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.embeddings import Embeddings
from langchain_core.tools import tool

from src.config import CHROMA_DIR, CURRICULUM_DIR, DATA_DIR, RETRIEVAL_BUDGET_MS
from src.fallback import unable_to_verify

CHUNK_SIZE = 500      # tokens (acceptance: explicit chunk size)
CHUNK_OVERLAP = 50    # tokens
DEFAULT_K = 4
COLLECTION = "sprints_curriculum"
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ---------------------------------------------------------------- Phase 1: load and parse (standard library)
def parse_front_matter(raw: str) -> Tuple[Dict[str, str], str]:
    """Split a curriculum file into (metadata, body).

    Curriculum:
        Module 6 (Document Ingestion and Embeddings): the load-and-parse phase keeps the module
        numbers and title so every answer can cite where it came from.
    """
    match = _FRONT.match(raw)
    if not match:
        return {}, raw
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, raw[match.end():]


def load_curriculum(folder: Path = CURRICULUM_DIR) -> List[Dict[str, Any]]:
    """Read every ``*.md`` curriculum file into one record per ``## section``.

    Curriculum:
        Module 6 (Document Ingestion and Embeddings): loading and metadata for the five-phase
        RAG pipeline.

    Args:
        folder: Folder with the curriculum Markdown files (each has ``title`` and ``modules`` front matter).

    Returns:
        ``[{"text": ..., "metadata": {"source", "title", "modules", "section"}}, ...]``; ``modules``
        is a string such as ``"6,7"`` (ChromaDB metadata must be a scalar).

    Raises:
        ValueError: A file lacks ``title`` or ``modules`` front matter.
    """
    records: List[Dict[str, Any]] = []
    for path in sorted(Path(folder).glob("*.md")):
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        if "title" not in meta or "modules" not in meta:
            raise ValueError(f"{path.name}: front matter needs 'title' and 'modules'")
        section, lines = "Overview", []

        def flush() -> None:
            """Emit one non-empty section while preserving its curriculum metadata.

            Curriculum:
                Module 6 (Document Ingestion and Embeddings): each section becomes an ingestible record.
                Module 7 (Vector Stores and Retrieval-Augmented Generation): metadata supports citations.
            """
            text = "\n".join(lines).strip()
            if text and not text.startswith(">"):
                records.append({"text": text, "metadata": {"source": path.name, "title": meta["title"],
                                                           "modules": meta["modules"], "section": section}})

        for line in body.splitlines():
            if line.startswith("## "):
                flush()
                section, lines = line[3:].strip(), []
            elif line.startswith("# "):
                continue
            else:
                lines.append(line)
        flush()
    return records


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (same method as pandas' default).

    Curriculum:
        Module 13 (Evaluator-Optimizer and Reflection Patterns): latency evidence is reported as
        p95 and maximum, not just an average, because the budget must hold consistently.
    """
    data = sorted(values)
    position = (len(data) - 1) * q
    low = int(math.floor(position))
    high = min(low + 1, len(data) - 1)
    return data[low] + (data[high] - data[low]) * (position - low)


# ---------------------------------------------------------------- Phases 2-4: chunk, embed, store
def build_splitter(chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP):
    """Return (splitter, measure, mode): 500-token chunks with a 50-token overlap.

    Curriculum:
        Module 6 (Document Ingestion and Embeddings): sentence-level separators make the overlap
        effective (an overlap can only carry units that fit inside it); sizes are in tokens.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    separators = ["\n## ", ". ", " ", ""]
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="cl100k_base", chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            separators=separators, keep_separator="end")
        return splitter, (lambda text: len(encoding.encode(text))), "tokens (cl100k_base)"
    except Exception:  # noqa: BLE001 - offline or blocked: fall back to characters / 4 and say so
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size * 4, chunk_overlap=chunk_overlap * 4, separators=separators,
            keep_separator="end")
        return splitter, (lambda text: len(text) // 4), "approx. tokens (characters / 4)"


def chunk_records(records: List[Dict[str, Any]], splitter) -> List[Dict[str, Any]]:
    """Split records into chunks that keep their metadata; the title is prefixed to the stored text.

    Curriculum:
        Module 7 (Vector Stores and Retrieval-Augmented Generation): a later chunk of a long section
        often never repeats its topic, so the document title is prepended to help retrieval.

    Returns:
        ``[{"text": "<title>. <chunk>", "metadata": {..., "chunk": n}}, ...]``
    """
    chunks: List[Dict[str, Any]] = []
    for record in records:
        for number, piece in enumerate(splitter.split_text(record["text"])):
            chunks.append({"text": f"{record['metadata']['title']}. {piece}",
                           "metadata": {**record["metadata"], "chunk": number}})
    return chunks


class HashingEmbeddings(Embeddings):
    """Offline lexical embeddings (hashed bag of words and word pairs); needs no download.

    Curriculum:
        Module 6 (Document Ingestion and Embeddings): a dependency-free fallback for environments
        where the model hub is unreachable. It matches shared words, not meaning.
    """

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _embed(self, text: str) -> List[float]:
        import hashlib
        words = re.findall(r"[a-z0-9]+", text.lower())
        grams = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        vec = [0.0] * self.dim
        for gram in grams:
            digest = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
            vec[digest % self.dim] += 1.0 if (digest >> 64) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed curriculum documents with deterministic lexical vectors.

        Curriculum:
            Module 6 (Document Ingestion and Embeddings): document text is mapped to vectors.
            Module 7 (Vector Stores and Retrieval-Augmented Generation): vectors support retrieval.
        """
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        """Embed a learner query in the same lexical vector space as indexed documents.

        Curriculum:
            Module 6 (Document Ingestion and Embeddings): query embedding uses the same model.
            Module 7 (Vector Stores and Retrieval-Augmented Generation): enables similarity search.
        """
        return self._embed(text)


def make_embeddings(provider: Optional[str] = None) -> Embeddings:
    """EMBEDDINGS_PROVIDER: ``fastembed`` (default, local model, downloads once), ``openai`` or ``hashing``.

    Curriculum:
        Module 6 (Document Ingestion and Embeddings): the embedding model must be the same at
        indexing time and at query time.
    """
    provider = (provider or os.environ.get("EMBEDDINGS_PROVIDER", "fastembed")).strip().lower()
    if provider == "hashing":
        return HashingEmbeddings()
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings()
    if provider == "fastembed":
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from langchain_community.embeddings import FastEmbedEmbeddings
        return FastEmbedEmbeddings()
    raise ValueError(f"Unsupported EMBEDDINGS_PROVIDER {provider!r} (use fastembed, openai or hashing)")


def build_index(records: Optional[List[Dict[str, Any]]] = None, embeddings: Optional[Embeddings] = None,
                persist_directory: Optional[Path] = CHROMA_DIR, collection: str = COLLECTION, reset: bool = True):
    """Chunk, embed and store the curriculum in ChromaDB; return the vector store.

    Curriculum:
        Module 6 (Document Ingestion and Embeddings): the offline phases of the RAG pipeline.
        Module 7 (Vector Stores and Retrieval-Augmented Generation): ChromaDB with metadata.

    Args:
        records: Output of :func:`load_curriculum` (loaded from ``data/curriculum_docs`` if omitted).
        embeddings: Embedding model (default: :func:`make_embeddings`).
        persist_directory: Where ChromaDB keeps the index; ``None`` keeps it in memory.
        collection: Collection name.
        reset: Delete an existing index folder first, so re-running never duplicates chunks.
    """
    from langchain_chroma import Chroma
    records = records if records is not None else load_curriculum()
    splitter, _measure, _mode = build_splitter()
    chunks = chunk_records(records, splitter)
    if reset and persist_directory and Path(persist_directory).exists():
        shutil.rmtree(persist_directory)
    store = Chroma(collection_name=collection, embedding_function=embeddings or make_embeddings(),
                   persist_directory=str(persist_directory) if persist_directory else None)
    store.add_texts([c["text"] for c in chunks], metadatas=[c["metadata"] for c in chunks],
                    ids=[f"{c['metadata']['source']}::{c['metadata']['section']}::{c['metadata']['chunk']}" for c in chunks])
    return store


_default_store = None


def get_store(persist_directory: Path = CHROMA_DIR, embeddings: Optional[Embeddings] = None):
    """Open the persisted ChromaDB index once and reuse it (loading is slow; querying is not).

    Curriculum:
        Module 7 (Vector Stores and Retrieval-Augmented Generation): one long-lived store object.
    """
    global _default_store
    if _default_store is None:
        from langchain_chroma import Chroma
        _default_store = Chroma(collection_name=COLLECTION, embedding_function=embeddings or make_embeddings(),
                                persist_directory=str(persist_directory))
    return _default_store


# ---------------------------------------------------------------- Phase 5: retrieve
def retrieve_curriculum(query: str, k: int = DEFAULT_K, store: Any = None,
                        budget_ms: float = RETRIEVAL_BUDGET_MS) -> Dict[str, Any]:
    """Return the curriculum passages most similar to ``query``, with timing.

    Curriculum:
        Module 7 (Vector Stores and Retrieval-Augmented Generation): similarity search with
        metadata so an answer can cite its sources; the elapsed time is measured on every call.
        Module 8 (Tools and Function Calling): never raises; failures return "Unable to verify".

    Args:
        query: The learner's question.
        k: Number of passages.
        store: A vector store (default: the persisted ChromaDB index).
        budget_ms: Latency budget (1000 ms by default).

    Returns:
        ``{"status": "OK", "passages": [...], "elapsed_ms": float, "within_budget": bool}``;
        ``{"status": "NO_CONTEXT", ...}`` when nothing was found; or the "Unable to verify" fallback
        (``RETRIEVAL_ERROR``) if the vector store fails.
    """
    started = time.perf_counter()
    try:
        results = (store or get_store()).similarity_search_with_score(str(query), k=k)
    except Exception as exc:  # noqa: BLE001 - any vector-store failure becomes a structured result
        return unable_to_verify("search_curriculum", f"the curriculum index could not be searched ({type(exc).__name__}: {exc})",
                                "RETRIEVAL_ERROR")
    elapsed_ms = (time.perf_counter() - started) * 1000
    passages = [{"text": doc.page_content, "source": doc.metadata.get("source"), "title": doc.metadata.get("title"),
                 "modules": [int(m) for m in str(doc.metadata.get("modules", "")).split(",") if m.strip().isdigit()],
                 "section": doc.metadata.get("section"), "distance": round(float(score), 4)} for doc, score in results]
    return {"status": "OK" if passages else "NO_CONTEXT", "passages": passages,
            "elapsed_ms": round(elapsed_ms, 1), "within_budget": elapsed_ms <= budget_ms}


def warm_up(store: Any = None) -> float:
    """Run one throw-away query so the embedding model is loaded; return how long that took (ms).

    Curriculum:
        Module 7 (Vector Stores and Retrieval-Augmented Generation): model loading must not be
        charged to a learner's first question.
    """
    started = time.perf_counter()
    retrieve_curriculum("warm up", k=1, store=store)
    return round((time.perf_counter() - started) * 1000, 1)


def benchmark_retrieval(queries: Sequence[str], store: Any = None, repeats: int = 5,
                        budget_ms: float = RETRIEVAL_BUDGET_MS) -> Dict[str, Any]:
    """Measure retrieval latency over many queries and report whether the budget holds.

    Curriculum:
        Module 7 (Vector Stores and Retrieval-Augmented Generation): the evidence for the
        "within 1000 milliseconds" acceptance criterion.
        Module 13 (Evaluator-Optimizer and Reflection Patterns): measures, then judges against a limit.

    Args:
        queries: Questions to run (each is run ``repeats`` times).
        store: Vector store to test (default: the persisted index).
        repeats: Repetitions per question.
        budget_ms: The limit.

    Returns:
        ``{"cold_start_ms", "n", "mean_ms", "p50_ms", "p95_ms", "max_ms", "over_budget", "budget_ms",
        "passed", "failures"}``. ``passed`` means every warmed-up query finished within the budget;
        the cold start is reported separately and is not part of ``passed``.
    """
    cold = warm_up(store)
    timings: List[float] = []
    failures: List[str] = []
    for _ in range(repeats):
        for query in queries:
            result = retrieve_curriculum(query, store=store, budget_ms=budget_ms)
            if result["status"] == "OK":
                timings.append(result["elapsed_ms"])
            else:
                failures.append(f"{query!r}: {result.get('message', result['status'])}")
    if not timings:
        return {"cold_start_ms": cold, "n": 0, "passed": False, "failures": failures, "budget_ms": budget_ms}
    over = sum(1 for t in timings if t > budget_ms)
    return {"cold_start_ms": cold, "n": len(timings), "mean_ms": round(statistics.mean(timings), 1),
            "p50_ms": round(percentile(timings, 0.5), 1), "p95_ms": round(percentile(timings, 0.95), 1),
            "max_ms": round(max(timings), 1), "over_budget": over, "budget_ms": budget_ms,
            "passed": over == 0 and not failures, "failures": failures}


@tool
def search_curriculum(query: str) -> str:
    """Search the Sprints curriculum and return JSON passages with their module numbers.

    Curriculum:
        Module 7 (Vector Stores and Retrieval-Augmented Generation): the RAG tool of the advisor.
        Module 9 (ReAct Agents): the observation the agent reads before it answers a question.

    Use it for any question about what the bootcamp teaches (chunking, LangGraph, tools, human-in-the-loop,
    placement rules, and so on). Answer ONLY from the returned passages and cite their module numbers. If
    the status is NO_CONTEXT, say the curriculum does not cover it. If the status is UNABLE_TO_VERIFY, the
    message starts with "Unable to verify": say so and do not answer from memory.
    """
    return json.dumps(retrieve_curriculum(query), ensure_ascii=False)

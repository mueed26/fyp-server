"""
Cross-referencing between lecture notes and past year papers.

KEY IDEA — this does NOT call any RPC or embedding API.

Every chunk (lecture AND past year) already has its `embedding vector(1536)`
stored in `document_chunks` from ingestion time. A past year paper is small
(tens of chunks), so instead of doing approximate nearest-neighbour search via
the HNSW-backed RPC once per chunk (expensive), we:

  1. Fetch lecture chunk embeddings + text        (1 DB read)
  2. Fetch past year chunk embeddings + text       (1 DB read)
  3. Compute the FULL cosine similarity matrix in numpy   (~1ms)
  4. For each lecture chunk, find its best-matching past year chunk

Cost per cross-reference = 2 DB reads + one matrix multiply.
No RPC calls. No OpenAI embedding calls. No LLM topic extraction.

This is chunk-to-chunk semantic matching — the highest-fidelity comparison
available — and it returns the ACTUAL matched past-year question text, so the
downstream generators can see "this lecture section corresponds to this real
exam question", not just a topic label.
"""

import json
from typing import List, Dict, Optional

import numpy as np

from src.services.supabase import supabase
from src.config.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# DOCUMENT ID HELPERS
# =============================================================================

def get_past_year_document_ids(project_id: str) -> List[str]:
    """Get document IDs tagged as past year papers in a project."""
    result = (
        supabase.table("project_documents")
        .select("id")
        .eq("project_id", project_id)
        .eq("source_tag", "past_year_paper")
        .eq("processing_status", "completed")
        .execute()
    )
    return [doc["id"] for doc in (result.data or [])]


def get_lecture_document_ids(project_id: str) -> List[str]:
    """Get document IDs tagged as lecture notes in a project."""
    result = (
        supabase.table("project_documents")
        .select("id")
        .eq("project_id", project_id)
        .eq("source_tag", "lecture_notes")
        .eq("processing_status", "completed")
        .execute()
    )
    return [doc["id"] for doc in (result.data or [])]


# =============================================================================
# CHUNK FETCHING
# =============================================================================

def _parse_embedding(raw) -> Optional[np.ndarray]:
    """
    pgvector columns come back from supabase-py either as a JSON-style string
    "[0.1, -0.2, ...]" or as a list. Normalise both into a float32 array.
    """
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return np.asarray(raw, dtype=np.float32)
    if isinstance(raw, str):
        try:
            return np.asarray(json.loads(raw), dtype=np.float32)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _readable_text(chunk: dict) -> str:
    """
    Prefer the raw original text (the actual lecture/exam wording) over the AI
    summary, because for cross-referencing we want to show the LLM the real
    question/content, not a paraphrase.
    """
    original = chunk.get("original_content")
    if isinstance(original, dict):
        text = original.get("text", "")
        if text:
            return text
    elif isinstance(original, str) and original:
        return original
    # Fallback to the AI summary content
    return chunk.get("content", "") or ""


def _fetch_chunks(doc_ids: List[str]) -> List[dict]:
    """
    Fetch chunks (embedding + text + page) for a set of documents in ONE query.
    Returns a list of dicts: {id, doc_id, embedding: np.ndarray, text, page}
    Chunks with unparseable/missing embeddings are skipped.
    """
    if not doc_ids:
        return []

    result = (
        supabase.table("document_chunks")
        .select("id, document_id, content, original_content, embedding, page_number, chunk_index")
        .in_("document_id", doc_ids)
        .order("chunk_index")
        .execute()
    )

    chunks = []
    for row in (result.data or []):
        emb = _parse_embedding(row.get("embedding"))
        if emb is None:
            continue
        chunks.append({
            "id": row.get("id"),
            "doc_id": row.get("document_id"),
            "embedding": emb,
            "text": _readable_text(row),
            "page": row.get("page_number", "Unknown"),
        })
    return chunks


# =============================================================================
# CORE CROSS-REFERENCE (numpy cosine matrix)
# =============================================================================

def _normalise(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise rows so a dot product gives cosine similarity directly."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-8, norms)  # guard against zero vectors
    return matrix / norms


def cross_reference_chunks(
    lecture_doc_ids: List[str],
    past_year_doc_ids: List[str],
    similarity_threshold: float = 0.45,
    max_linkages: int = 20,
) -> Optional[Dict]:
    """
    Compare every lecture chunk against every past year chunk using their
    pre-computed embeddings, entirely in numpy.

    Args:
        lecture_doc_ids: document IDs tagged lecture_notes (selected by user)
        past_year_doc_ids: document IDs tagged past_year_paper (selected by user)
        similarity_threshold: cosine cutoff for "this lecture content appeared
            in the exam". For text-embedding-3-large, on-topic chunk pairs sit
            around 0.45-0.65, near-duplicates 0.7+. 0.45 is a sensible default.
        max_linkages: cap on how many linkages we surface to the prompt (token budget)

    Returns a rich linkage structure, or None if cross-referencing isn't possible:
        {
          "linkages": [
            {
              "lecture_excerpt": "...real lecture text...",
              "exam_excerpt": "...the matched past year question text...",
              "similarity": 0.87,
              "lecture_page": 12,
              "exam_page": 3,
            }, ...   # one per matched lecture chunk, sorted by similarity desc
          ],
          "exam_coverage_score": 0.62,   # fraction of lecture chunks with an exam match
          "matched_lecture_chunks": 31,
          "total_lecture_chunks": 50,
          "total_exam_chunks": 28,
        }
    """
    if not lecture_doc_ids or not past_year_doc_ids:
        logger.warning(
            "cross_reference_skipped",
            reason="missing_doc_ids",
            lecture_docs=len(lecture_doc_ids),
            past_year_docs=len(past_year_doc_ids),
        )
        return None

    lecture_chunks = _fetch_chunks(lecture_doc_ids)
    exam_chunks = _fetch_chunks(past_year_doc_ids)

    logger.info(
        "cross_reference_chunks_fetched",
        lecture_chunks=len(lecture_chunks),
        exam_chunks=len(exam_chunks),
    )

    if not lecture_chunks or not exam_chunks:
        logger.warning("cross_reference_no_usable_chunks")
        return None

    # Build normalised matrices: L (n_lecture x 1536), P (n_exam x 1536)
    L = _normalise(np.vstack([c["embedding"] for c in lecture_chunks]))
    P = _normalise(np.vstack([c["embedding"] for c in exam_chunks]))

    # Cosine similarity matrix: (n_lecture x n_exam). This is the whole job.
    sim = L @ P.T

    # For each lecture chunk, its single best-matching exam chunk + score
    best_exam_idx = np.argmax(sim, axis=1)
    best_sim = np.max(sim, axis=1)

    linkages = []
    matched = 0
    for i, lecture_chunk in enumerate(lecture_chunks):
        score = float(best_sim[i])
        if score < similarity_threshold:
            continue
        matched += 1
        exam_chunk = exam_chunks[int(best_exam_idx[i])]
        linkages.append({
            "lecture_excerpt": lecture_chunk["text"][:300],
            "exam_excerpt": exam_chunk["text"][:300],
            "similarity": round(score, 3),
            "lecture_page": lecture_chunk["page"],
            "exam_page": exam_chunk["page"],
        })

    # Highest-similarity linkages first; cap for token budget
    linkages.sort(key=lambda x: x["similarity"], reverse=True)
    linkages = linkages[:max_linkages]

    coverage = round(matched / len(lecture_chunks), 3) if lecture_chunks else 0.0

    logger.info(
        "cross_reference_completed",
        matched_lecture_chunks=matched,
        total_lecture_chunks=len(lecture_chunks),
        surfaced_linkages=len(linkages),
        exam_coverage_score=coverage,
    )

    return {
        "linkages": linkages,
        "exam_coverage_score": coverage,
        "matched_lecture_chunks": matched,
        "total_lecture_chunks": len(lecture_chunks),
        "total_exam_chunks": len(exam_chunks),
    }


# =============================================================================
# PROMPT FORMATTING HELPERS (used by the feature generators)
# =============================================================================

def format_linkages_for_prompt(linkage_result: Optional[Dict]) -> str:
    """
    Turn the linkage structure into a readable block for an LLM prompt.
    Each entry pairs real lecture content with the real exam question it matched.
    """
    if not linkage_result or not linkage_result.get("linkages"):
        return "No past year exam linkages found."

    lines = [
        f"(Exam coverage: {int(linkage_result['exam_coverage_score'] * 100)}% of lecture "
        f"content has appeared in past exams - {linkage_result['matched_lecture_chunks']}"
        f"/{linkage_result['total_lecture_chunks']} sections matched.)\n"
    ]
    for i, link in enumerate(linkage_result["linkages"], 1):
        lines.append(
            f"[{i}] similarity {link['similarity']:.2f}\n"
            f"    LECTURE (p.{link['lecture_page']}): {link['lecture_excerpt']}\n"
            f"    APPEARED IN EXAM (p.{link['exam_page']}): {link['exam_excerpt']}"
        )
    return "\n\n".join(lines)


def get_matched_exam_text(linkage_result: Optional[Dict]) -> str:
    """
    Deduplicated past-year exam excerpts that actually matched lecture content.
    This is the pre-filtered "relevant exam questions" text - far better than
    dumping the entire exam paper, because it's already scoped to the lecture.
    """
    if not linkage_result or not linkage_result.get("linkages"):
        return ""
    seen = set()
    excerpts = []
    for link in linkage_result["linkages"]:
        ex = link["exam_excerpt"]
        if ex and ex not in seen:
            seen.add(ex)
            excerpts.append(ex)
    return "\n\n".join(excerpts)
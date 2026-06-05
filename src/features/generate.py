"""
Feature Generation Orchestrator.

Supported features: summary, flashcards, practice_questions, mind_map

When both lecture notes and past year papers are selected, runs chunk-to-chunk
cross-referencing (see cross_reference.py) ONCE and passes the resulting exam
linkages to every generator. No RPC / embedding calls are made for this.
"""

from typing import List, Dict, Optional, Tuple

from src.services.supabase import supabase
from src.features.utils import get_document_chunks_content
from src.features.summary import generate_summary
from src.features.mind_map import generate_mind_map
from src.features.flashcards import generate_flashcards
from src.features.practice_questions import generate_practice_questions
from src.features.cross_reference import cross_reference_chunks
from src.config.logging import get_logger, set_project_id

logger = get_logger(__name__)

SUPPORTED_FEATURES = ["summary", "flashcards", "practice_questions", "mind_map"]


def _update_features_status(document_id: str, status: str, updates: dict = None):
    """Update features_status and optionally other columns on project_documents."""
    data = {"features_status": status}
    if updates:
        data.update(updates)
    supabase.table("project_documents").update(data).eq("id", document_id).execute()


def _separate_doc_ids(doc_ids: List[str]) -> Tuple[List[str], List[str]]:
    """
    Split a list of document IDs into (lecture_doc_ids, past_year_doc_ids)
    in a SINGLE batched query instead of one query per document.
    """
    if not doc_ids:
        return [], []

    result = (
        supabase.table("project_documents")
        .select("id, source_tag")
        .in_("id", doc_ids)
        .execute()
    )

    lecture_ids, past_year_ids = [], []
    for row in (result.data or []):
        if row.get("source_tag") == "past_year_paper":
            past_year_ids.append(row["id"])
        else:
            lecture_ids.append(row["id"])

    logger.info(
        "doc_ids_separated",
        total=len(doc_ids),
        lecture_count=len(lecture_ids),
        past_year_count=len(past_year_ids),
    )
    return lecture_ids, past_year_ids


def generate_single_feature_for_document(document_id: str, feature_type: str) -> str:
    """
    Generate a single feature for ONE document on-demand (no exam context).
    Supported: summary, flashcards, practice_questions, mind_map
    """
    if feature_type not in SUPPORTED_FEATURES:
        raise Exception(f"Unsupported feature type: {feature_type}. Must be one of {SUPPORTED_FEATURES}")

    logger.info("generating_single_feature", document_id=document_id, feature_type=feature_type)

    contents = get_document_chunks_content(document_id)
    if not contents:
        raise Exception(f"No chunks found for document {document_id}")

    if feature_type == "summary":
        result = generate_summary(contents)

    elif feature_type == "mind_map":
        # Mind map is built on top of the summary
        doc_result = (
            supabase.table("project_documents")
            .select("summary")
            .eq("id", document_id)
            .execute()
        )
        existing_summary = doc_result.data[0].get("summary") if doc_result.data else None
        if not existing_summary:
            logger.info("generating_summary_for_mind_map", document_id=document_id)
            existing_summary = generate_summary(contents)
            supabase.table("project_documents").update(
                {"summary": existing_summary}
            ).eq("id", document_id).execute()
        result = generate_mind_map(existing_summary)

    elif feature_type == "flashcards":
        result = generate_flashcards("\n\n".join(contents))

    elif feature_type == "practice_questions":
        result = generate_practice_questions("\n\n".join(contents))

    supabase.table("project_documents").update(
        {feature_type: result}
    ).eq("id", document_id).execute()

    logger.info("single_feature_generated", document_id=document_id, feature_type=feature_type, result_length=len(result))
    return result


def generate_exam_aware_feature(
    project_id: str,
    doc_ids: List[str],
    feature_type: str,
) -> str:
    """
    Generate a feature with exam-aware cross-referencing.

    Flow:
    1. Separate doc_ids into lecture vs past year   (1 query)
    2. Collect lecture content from chunks
    3. Cross-reference lecture <-> past year chunks (2 queries + numpy, no RPC)
    4. Generate the feature, passing the exam linkages through
    """
    set_project_id(project_id)

    if feature_type not in SUPPORTED_FEATURES:
        raise Exception(f"Unsupported feature type: {feature_type}. Must be one of {SUPPORTED_FEATURES}")

    logger.info(
        "exam_aware_generation_started",
        project_id=project_id,
        feature_type=feature_type,
        doc_count=len(doc_ids),
    )

    # Step 1: separate
    lecture_doc_ids, past_year_doc_ids = _separate_doc_ids(doc_ids)

    # Step 2: collect lecture content (fall back to past year if no lecture docs)
    lecture_contents = []
    for doc_id in lecture_doc_ids:
        lecture_contents.extend(get_document_chunks_content(doc_id))

    if not lecture_contents and past_year_doc_ids:
        logger.warning("no_lecture_docs_using_past_year_as_fallback")
        for doc_id in past_year_doc_ids:
            lecture_contents.extend(get_document_chunks_content(doc_id))

    if not lecture_contents:
        raise Exception("No content found in selected documents")

    lecture_content_str = "\n\n".join(lecture_contents)
    logger.info(
        "lecture_content_collected",
        chunk_count=len(lecture_contents),
        total_chars=len(lecture_content_str),
    )

    # Step 3: cross-reference (numpy chunk-to-chunk; None if no past year docs)
    exam_linkages = None
    if past_year_doc_ids and lecture_doc_ids:
        exam_linkages = cross_reference_chunks(
            lecture_doc_ids=lecture_doc_ids,
            past_year_doc_ids=past_year_doc_ids,
        )

    # Step 4: generate
    logger.info(
        "generating_feature",
        feature_type=feature_type,
        has_exam_linkages=exam_linkages is not None,
    )

    if feature_type == "summary":
        result = generate_summary(lecture_contents, exam_linkages=exam_linkages)

    elif feature_type == "flashcards":
        result = generate_flashcards(lecture_content_str, exam_linkages=exam_linkages)

    elif feature_type == "practice_questions":
        result = generate_practice_questions(lecture_content_str, exam_linkages=exam_linkages)

    elif feature_type == "mind_map":
        # Mind map is built on the (exam-aware) summary
        summary = generate_summary(lecture_contents, exam_linkages=exam_linkages)
        result = generate_mind_map(summary)

    logger.info("exam_aware_generation_completed", feature_type=feature_type, result_length=len(result))
    return result


def generate_all_features_for_document(document_id: str):
    """
    Generate all supported features for a single document after ingestion.
    Order: summary -> mind_map -> flashcards -> practice_questions
    (No exam context here - this is per single document at ingestion time.)
    """
    logger.info("generate_all_features_started", document_id=document_id)
    try:
        _update_features_status(document_id, "processing")

        contents = get_document_chunks_content(document_id)
        if not contents:
            logger.warning("no_chunks_found_for_features", document_id=document_id)
            _update_features_status(document_id, "failed")
            return {"success": False, "error": "No chunks found for document"}

        lecture_content_str = "\n\n".join(contents)

        logger.info("generating_summary", document_id=document_id)
        _update_features_status(document_id, "generating_summary")
        summary = generate_summary(contents)
        supabase.table("project_documents").update({"summary": summary}).eq("id", document_id).execute()
        logger.info("summary_done", document_id=document_id, chars=len(summary))

        logger.info("generating_mind_map", document_id=document_id)
        _update_features_status(document_id, "generating_mind_map")
        mind_map = generate_mind_map(summary)
        supabase.table("project_documents").update({"mind_map": mind_map}).eq("id", document_id).execute()
        logger.info("mind_map_done", document_id=document_id)

        logger.info("generating_flashcards", document_id=document_id)
        _update_features_status(document_id, "generating_flashcards")
        flashcards = generate_flashcards(lecture_content_str)
        supabase.table("project_documents").update({"flashcards": flashcards}).eq("id", document_id).execute()
        logger.info("flashcards_done", document_id=document_id)

        logger.info("generating_practice_questions", document_id=document_id)
        _update_features_status(document_id, "generating_practice_questions")
        practice_questions = generate_practice_questions(lecture_content_str)
        supabase.table("project_documents").update({"practice_questions": practice_questions}).eq("id", document_id).execute()
        logger.info("practice_questions_done", document_id=document_id)

        _update_features_status(document_id, "completed")
        logger.info("generate_all_features_completed", document_id=document_id)
        return {"success": True, "document_id": document_id}

    except Exception as e:
        logger.error("generate_all_features_failed", document_id=document_id, error=str(e), exc_info=True)
        _update_features_status(document_id, "failed")
        raise Exception(f"Failed to generate features for document {document_id}: {str(e)}")
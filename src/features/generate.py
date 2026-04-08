"""
Feature Generation Orchestrator.

Handles all feature generation including exam-aware cross-referencing.
When both lecture notes and past year papers are selected, uses RAG
vector similarity search to identify exam-relevant topics.
"""

from typing import List, Dict, Optional, Tuple

from src.services.supabase import supabase
from src.features.utils import get_document_chunks_content
from src.features.summary import generate_summary
from src.features.faq import generate_faq
from src.features.study_guide import generate_study_guide
from src.features.briefing_doc import generate_briefing_doc
from src.features.mind_map import generate_mind_map
from src.features.flashcards import generate_flashcards
from src.features.practice_questions import generate_practice_questions
from src.features.cross_reference import (
    get_past_year_content,
    extract_key_topics,
    cross_reference_topics,
)


def _update_features_status(document_id: str, status: str, updates: dict = None):
    """Update features_status and optionally other columns on project_documents."""
    data = {"features_status": status}
    if updates:
        data.update(updates)
    supabase.table("project_documents").update(data).eq("id", document_id).execute()


def _get_exam_context(
    project_id: str,
    doc_ids: List[str],
    lecture_content: str,
) -> Tuple[Optional[str], Optional[Dict[str, bool]]]:
    """
    Check if there are past year papers in the selected documents.
    If so, perform RAG cross-referencing and return past year content + topic relevance.
    
    Returns:
        (past_year_content, exam_relevant_topics) or (None, None) if no past year papers
    """
    # Check which selected docs are past year papers
    past_year_doc_ids = []
    for doc_id in doc_ids:
        result = (
            supabase.table("project_documents")
            .select("id, source_tag")
            .eq("id", doc_id)
            .execute()
        )
        if result.data and result.data[0].get("source_tag") == "past_year_paper":
            past_year_doc_ids.append(doc_id)

    if not past_year_doc_ids:
        return None, None

    # Get past year content
    past_year_content = get_past_year_content(past_year_doc_ids)
    if not past_year_content:
        return None, None

    # Extract key topics from lecture content
    topics = extract_key_topics(lecture_content)
    if not topics:
        return past_year_content, None

    # Cross-reference topics against past year papers using RAG vector search
    exam_relevant_topics = cross_reference_topics(
        lecture_doc_ids=[d for d in doc_ids if d not in past_year_doc_ids],
        past_year_doc_ids=past_year_doc_ids,
        topics=topics,
    )

    return past_year_content, exam_relevant_topics


def generate_single_feature_for_document(document_id: str, feature_type: str) -> str:
    """
    Generate a single feature for a document on-demand.
    """
    contents = get_document_chunks_content(document_id)
    if not contents:
        raise Exception("No chunks found for document")

    if feature_type == "summary":
        result = generate_summary(contents)
    elif feature_type == "faq":
        result = generate_faq(contents)
    elif feature_type == "study_guide":
        result = generate_study_guide(contents)
    elif feature_type == "briefing_doc":
        result = generate_briefing_doc(contents)
    elif feature_type == "mind_map":
        doc_result = (
            supabase.table("project_documents")
            .select("study_guide")
            .eq("id", document_id)
            .execute()
        )
        study_guide = doc_result.data[0].get("study_guide") if doc_result.data else None
        if not study_guide:
            study_guide = generate_study_guide(contents)
            supabase.table("project_documents").update(
                {"study_guide": study_guide}
            ).eq("id", document_id).execute()
        result = generate_mind_map(study_guide)
    elif feature_type == "flashcards":
        lecture_content = "\n\n".join(contents)
        result = generate_flashcards(lecture_content)
    elif feature_type == "practice_questions":
        lecture_content = "\n\n".join(contents)
        result = generate_practice_questions(lecture_content)
    else:
        raise Exception(f"Unknown feature type: {feature_type}")

    # Store result in DB
    supabase.table("project_documents").update(
        {feature_type: result}
    ).eq("id", document_id).execute()

    return result


def generate_exam_aware_feature(
    project_id: str,
    doc_ids: List[str],
    feature_type: str,
) -> str:
    """
    Generate a feature with exam-aware cross-referencing.
    
    This is the main entry point for feature generation when multiple
    documents (lecture notes + past year papers) are involved.
    
    1. Collects content from all selected documents
    2. Separates lecture notes from past year papers
    3. Performs RAG cross-referencing to find exam-relevant topics
    4. Generates the feature with exam context
    """
    # Collect lecture content (from non-past-year docs)
    lecture_contents = []
    lecture_doc_ids = []
    past_year_doc_ids = []

    for doc_id in doc_ids:
        doc_result = (
            supabase.table("project_documents")
            .select("id, source_tag")
            .eq("id", doc_id)
            .execute()
        )
        if doc_result.data:
            tag = doc_result.data[0].get("source_tag", "lecture_notes")
            if tag == "past_year_paper":
                past_year_doc_ids.append(doc_id)
            else:
                lecture_doc_ids.append(doc_id)

    # Get lecture content
    for doc_id in lecture_doc_ids:
        chunks = get_document_chunks_content(doc_id)
        lecture_contents.extend(chunks)

    # If no lecture content but we have past year papers, use past year as content
    if not lecture_contents and past_year_doc_ids:
        for doc_id in past_year_doc_ids:
            chunks = get_document_chunks_content(doc_id)
            lecture_contents.extend(chunks)

    if not lecture_contents:
        raise Exception("No content found in selected documents")

    lecture_content = "\n\n".join(lecture_contents)

    # Get exam context via RAG cross-referencing
    past_year_content, exam_relevant_topics = _get_exam_context(
        project_id, doc_ids, lecture_content
    )

    # Generate the feature
    if feature_type == "summary":
        result = generate_summary(
            lecture_contents,
            past_year_content=past_year_content,
            exam_relevant_topics=exam_relevant_topics,
        )
    elif feature_type == "flashcards":
        result = generate_flashcards(
            lecture_content,
            past_year_content=past_year_content,
            exam_relevant_topics=exam_relevant_topics,
        )
    elif feature_type == "practice_questions":
        result = generate_practice_questions(
            lecture_content,
            past_year_content=past_year_content,
            exam_relevant_topics=exam_relevant_topics,
        )
    elif feature_type == "mind_map":
        # Generate study guide first, then mind map
        study_guide = generate_study_guide(lecture_contents)
        result = generate_mind_map(study_guide)
    elif feature_type == "briefing_doc":
        result = generate_briefing_doc(lecture_contents)
    elif feature_type == "faq":
        result = generate_faq(lecture_contents)
    elif feature_type == "study_guide":
        result = generate_study_guide(lecture_contents)
    else:
        raise Exception(f"Unknown feature type: {feature_type}")

    return result


def generate_all_features_for_document(document_id: str):
    """
    Generate all features for a single document. Called by Celery after ingestion.
    """
    try:
        _update_features_status(document_id, "processing")

        contents = get_document_chunks_content(document_id)
        if not contents:
            _update_features_status(document_id, "failed")
            return {"success": False, "error": "No chunks found for document"}

        _update_features_status(document_id, "generating_summary")
        summary = generate_summary(contents)
        _update_features_status(document_id, "completed", {"summary": summary})

        return {"success": True, "document_id": document_id}

    except Exception as e:
        _update_features_status(document_id, "failed")
        raise Exception(f"Failed to generate features for document {document_id}: {str(e)}")
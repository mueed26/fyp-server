"""
Feature Generation Routes.

Endpoints:
  POST   /api/projects/{project_id}/features/generate
  POST   /api/projects/{project_id}/features/merge
  POST   /api/projects/{project_id}/sources/{source_id}/expand
  GET    /api/projects/{project_id}/documents/{document_id}/features
  GET    /api/projects/{project_id}/sources
  DELETE /api/projects/{project_id}/sources/{source_id}
  PUT    /api/projects/{project_id}/files/{document_id}/tag
  POST   /api/projects/{project_id}/quiz/evaluate

Supported feature types: summary, flashcards, practice_questions, mind_map
"""

import json
from typing import List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Depends
from src.services.supabase import supabase
from src.services.clerkAuth import get_current_user_clerk_id
from src.services.llm import openAI
from src.features.generate import generate_single_feature_for_document, generate_exam_aware_feature
from src.features.utils import merge_contents, generate_title
from src.features.mind_map import generate_mind_map
from src.config.logging import get_logger, set_project_id, set_user_id
from langchain_core.prompts import ChatPromptTemplate

logger = get_logger(__name__)

router = APIRouter(tags=["featureRoutes"])

SUPPORTED_FEATURES = ["summary", "flashcards", "practice_questions", "mind_map"]


# =============================================================================
# REQUEST / RESPONSE MODELS
# =============================================================================

class FeatureGenerateRequest(BaseModel):
    doc_ids: List[str] = Field(..., description="List of document IDs")
    feature_type: str = Field(..., description="summary, flashcards, practice_questions, mind_map")


class FeatureMergeRequest(BaseModel):
    doc_ids: List[str] = Field(..., description="List of document IDs to merge features from")
    source_type: str = Field(..., description="summary, flashcards, practice_questions, mind_map")


class DocumentTagRequest(BaseModel):
    source_tag: str = Field(..., description="Either 'lecture_notes' or 'past_year_paper'")


class QuizEvaluateRequest(BaseModel):
    question: str = Field(..., description="The question text")
    model_answer: str = Field(..., description="The correct/model answer")
    user_answer: str = Field(..., description="The student's submitted answer")
    question_type: str = Field(..., description="'short_answer' or 'paragraph'")
    max_marks: int = Field(10, description="Maximum marks for this question")


class QuizEvaluation(BaseModel):
    """Structured grading output produced by the LLM."""
    awarded_marks: float = Field(description="Marks awarded, between 0 and max_marks (0.5 steps allowed)")
    verdict: str = Field(description="One of: correct, partial, incorrect")
    feedback: str = Field(description="2-3 sentences of encouraging but honest feedback")
    strengths: List[str] = Field(description="Up to 3 things the student got right")
    improvements: List[str] = Field(description="Up to 3 things the student could improve")


# =============================================================================
# HELPERS
# =============================================================================

def _clean_json(content: str) -> str:
    content = str(content).strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


# =============================================================================
# DOCUMENT TAGGING
# =============================================================================

@router.put("/{project_id}/files/{document_id}/tag")
async def tag_document(
    project_id: str,
    document_id: str,
    request: DocumentTagRequest,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """Tag a document as lecture_notes or past_year_paper."""
    set_project_id(project_id)
    set_user_id(current_user_clerk_id)
    try:
        logger.info("tagging_document", document_id=document_id, source_tag=request.source_tag)

        valid_tags = ["lecture_notes", "past_year_paper"]
        if request.source_tag not in valid_tags:
            logger.warning("invalid_source_tag", document_id=document_id, source_tag=request.source_tag)
            raise HTTPException(status_code=400, detail=f"Invalid source_tag. Must be one of: {valid_tags}")

        result = (
            supabase.table("project_documents")
            .update({"source_tag": request.source_tag})
            .eq("id", document_id)
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )

        if not result.data:
            logger.warning("document_not_found_for_tagging", document_id=document_id)
            raise HTTPException(status_code=404, detail="Document not found")

        logger.info("document_tagged_successfully", document_id=document_id, source_tag=request.source_tag)
        return {"message": "Document tagged successfully", "data": result.data[0]}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("document_tagging_error", document_id=document_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error tagging document: {str(e)}")


# =============================================================================
# FEATURE GENERATION (exam-aware)
# =============================================================================

@router.post("/{project_id}/features/generate")
async def generate_features(
    project_id: str,
    request: FeatureGenerateRequest,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """Generate a feature for the given documents (exam-aware when past year papers are selected)."""
    set_project_id(project_id)
    set_user_id(current_user_clerk_id)
    try:
        feature_type = request.feature_type
        logger.info("generating_features", feature_type=feature_type, doc_count=len(request.doc_ids))

        if feature_type not in SUPPORTED_FEATURES:
            logger.warning("invalid_feature_type", feature_type=feature_type)
            raise HTTPException(status_code=400, detail=f"Invalid feature_type. Must be one of: {SUPPORTED_FEATURES}")

        if not request.doc_ids:
            logger.warning("no_doc_ids_provided", feature_type=feature_type)
            raise HTTPException(status_code=400, detail="doc_ids is required")

        # Single batched query to discover which selected docs are past year papers
        tags_result = (
            supabase.table("project_documents")
            .select("id, source_tag")
            .in_("id", request.doc_ids)
            .execute()
        )
        has_past_year = any(row.get("source_tag") == "past_year_paper" for row in (tags_result.data or []))

        exam_aware_types = ["flashcards", "practice_questions", "summary"]

        if feature_type in exam_aware_types and has_past_year:
            logger.info("exam_aware_generation_started", feature_type=feature_type, doc_count=len(request.doc_ids))
            result = generate_exam_aware_feature(
                project_id=project_id,
                doc_ids=request.doc_ids,
                feature_type=feature_type,
            )

            # Store the generated content on the first lecture-notes document
            for row in (tags_result.data or []):
                if row.get("source_tag") != "past_year_paper":
                    supabase.table("project_documents").update(
                        {feature_type: result}
                    ).eq("id", row["id"]).execute()
                    logger.info("exam_aware_feature_stored", feature_type=feature_type, document_id=row["id"])
                    break

            logger.info("exam_aware_generation_completed", feature_type=feature_type)
            return {
                "message": "Feature generation complete (exam-aware with RAG cross-referencing)",
                "status": "ready_to_generate_source",
                "feature_type": feature_type,
                "cross_referenced": True,
                "generated_count": 1,
                "total_docs": len(request.doc_ids),
            }

        # Standard per-document generation
        generated_count = 0
        for doc_id in request.doc_ids:
            doc_result = (
                supabase.table("project_documents")
                .select(f"id, {feature_type}")
                .eq("id", doc_id)
                .eq("project_id", project_id)
                .eq("clerk_id", current_user_clerk_id)
                .execute()
            )
            if not doc_result.data:
                logger.warning("doc_not_found_skipping", document_id=doc_id, feature_type=feature_type)
                continue

            existing_value = doc_result.data[0].get(feature_type)
            if not existing_value:
                logger.info("generating_feature_for_document", document_id=doc_id, feature_type=feature_type)
                generate_single_feature_for_document(doc_id, feature_type)
                generated_count += 1
            else:
                logger.info("feature_already_exists_skipping", document_id=doc_id, feature_type=feature_type)

        logger.info("feature_generation_completed", feature_type=feature_type,
                    generated_count=generated_count, total_docs=len(request.doc_ids))
        return {
            "message": "Feature generation complete",
            "status": "ready_to_generate_source",
            "feature_type": feature_type,
            "cross_referenced": False,
            "generated_count": generated_count,
            "total_docs": len(request.doc_ids),
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("feature_generation_error", feature_type=request.feature_type, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error generating features: {str(e)}")


# =============================================================================
# MERGE INTO GENERATED SOURCE — FIXED VERSION
# =============================================================================

@router.post("/{project_id}/features/merge")
async def merge_features(
    project_id: str,
    request: FeatureMergeRequest,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """
    Merge features from documents into a generated_source.

    🔥 BUG FIX:
    When the user selects lecture + past_year docs and generates exam-aware
    flashcards/practice_questions/summary, the EXAM-AWARE result is stored
    ONLY on the lecture document (see generate_features above).

    Meanwhile, the past_year doc still has its own non-exam-aware flashcards
    from when it was first ingested.

    The OLD merge_features looped over BOTH docs and grabbed contents[0] —
    which sometimes returned the stale generic flashcards from the past_year
    doc instead of the exam-aware ones from the lecture doc.

    FIX: When past_year is selected, only read the feature content from
    lecture docs. Their content is the exam-aware version.
    """
    set_project_id(project_id)
    set_user_id(current_user_clerk_id)
    try:
        source_type = request.source_type
        logger.info("merging_features", source_type=source_type, doc_count=len(request.doc_ids))

        if source_type not in SUPPORTED_FEATURES:
            logger.warning("invalid_source_type", source_type=source_type)
            raise HTTPException(status_code=400, detail=f"Invalid source_type. Must be one of: {SUPPORTED_FEATURES}")

        if not request.doc_ids:
            logger.warning("no_doc_ids_provided_for_merge", source_type=source_type)
            raise HTTPException(status_code=400, detail="doc_ids is required")

        # mind_map is built on summary content
        db_column = "summary" if source_type == "mind_map" else source_type

        # ──────────────────────────────────────────────────────────────────
        #  Identify lecture vs past_year docs FIRST
        # ──────────────────────────────────────────────────────────────────
        tags_result = (
            supabase.table("project_documents")
            .select("id, source_tag")
            .in_("id", request.doc_ids)
            .execute()
        )

        lecture_doc_ids = []
        past_year_doc_ids = []
        for row in (tags_result.data or []):
            if row.get("source_tag") == "past_year_paper":
                past_year_doc_ids.append(row["id"])
            else:
                lecture_doc_ids.append(row["id"])

        has_past_year = len(past_year_doc_ids) > 0
        exam_aware_types = ["flashcards", "practice_questions", "summary", "mind_map"]

        # When past_year is selected AND we have lecture docs, ONLY read from
        # lecture docs. The exam-aware result was stored on the first lecture
        # doc by /features/generate.
        if has_past_year and lecture_doc_ids and source_type in exam_aware_types:
            doc_ids_to_read = lecture_doc_ids
            logger.info(
                "merge_using_lecture_docs_only_due_to_exam_awareness",
                source_type=source_type,
                lecture_count=len(lecture_doc_ids),
                excluded_past_year_count=len(past_year_doc_ids),
            )
        else:
            doc_ids_to_read = request.doc_ids
            logger.info(
                "merge_using_all_selected_docs",
                source_type=source_type,
                doc_count=len(doc_ids_to_read),
            )

        # ──────────────────────────────────────────────────────────────────
        #  Read the feature content from the chosen docs
        # ──────────────────────────────────────────────────────────────────
        contents = []
        for doc_id in doc_ids_to_read:
            doc_result = (
                supabase.table("project_documents")
                .select(f"id, filename, {db_column}, source_tag")
                .eq("id", doc_id)
                .eq("project_id", project_id)
                .eq("clerk_id", current_user_clerk_id)
                .execute()
            )
            if doc_result.data and doc_result.data[0].get(db_column):
                contents.append({
                    "title": doc_result.data[0].get("filename"),
                    "content": doc_result.data[0].get(db_column),
                    "source_tag": doc_result.data[0].get("source_tag"),
                })

        if not contents:
            logger.warning(
                "no_feature_content_found_for_merge",
                source_type=source_type,
                doc_count=len(doc_ids_to_read),
                lecture_count=len(lecture_doc_ids),
                past_year_count=len(past_year_doc_ids),
            )
            raise HTTPException(
                status_code=400,
                detail=f"No {source_type} content found. Generate features first.",
            )

        logger.info(
            "feature_content_fetched",
            source_type=source_type,
            content_count=len(contents),
            from_source_tags=[c.get("source_tag") for c in contents],
        )

        # ──────────────────────────────────────────────────────────────────
        #  Build the final content
        # ──────────────────────────────────────────────────────────────────
        if source_type in ["flashcards", "practice_questions"]:
            final_content = contents[0]["content"]
            title_input = contents[0].get("title", "Practice Material")[:200]

        elif source_type == "mind_map":
            summary_text = (
                contents[0]["content"]
                if len(contents) == 1
                else merge_contents(contents, "summary")
            )
            logger.info("generating_mind_map_for_merge", source_type=source_type)
            final_content = generate_mind_map(summary_text)
            title_input = summary_text[:2000]

        elif len(contents) == 1:
            final_content = contents[0]["content"]
            title_input = final_content[:2000]

        else:
            logger.info(
                "merging_multiple_contents",
                source_type=source_type,
                content_count=len(contents),
            )
            final_content = merge_contents(contents, source_type)
            title_input = final_content[:2000]

        title = generate_title(title_input)
        logger.info("title_generated", title=title, source_type=source_type)

        source_data = {
            "project_id": project_id,
            "clerk_id": current_user_clerk_id,
            "title": title,
            "source_type": source_type,
            "content": final_content,
            "document_ids": request.doc_ids,  # keep ALL selected docs, so /expand can re-cross-reference
            "total_sources": len(request.doc_ids),
        }

        result = supabase.table("generated_sources").insert(source_data).execute()
        if not result.data:
            logger.error("generated_source_creation_failed", source_type=source_type, reason="no_data_returned")
            raise HTTPException(status_code=422, detail="Failed to create generated source")

        logger.info(
            "features_merged_successfully",
            source_type=source_type,
            source_id=result.data[0]["id"],
            had_past_year=has_past_year,
        )
        return {"message": f"Generated {source_type} source created successfully", "data": result.data[0]}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("feature_merge_error", source_type=request.source_type, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error merging features: {str(e)}")


# =============================================================================
# EXPAND — generate more cards / questions and append to existing source
# =============================================================================

@router.post("/{project_id}/sources/{source_id}/expand")
async def expand_source(
    project_id: str,
    source_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """
    Generate a fresh batch of flashcards or practice questions and append them
    to an existing generated_source record.
    """
    set_project_id(project_id)
    set_user_id(current_user_clerk_id)
    try:
        from src.features.cross_reference import cross_reference_chunks, format_linkages_for_prompt

        # ── 1. Fetch the existing source record ──────────────────────────────
        source_result = (
            supabase.table("generated_sources")
            .select("*")
            .eq("id", source_id)
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )
        if not source_result.data:
            raise HTTPException(status_code=404, detail="Source not found")

        source = source_result.data[0]
        source_type = source["source_type"]
        expand_count = source.get("expand_count", 0)

        if source_type not in ["flashcards", "practice_questions"]:
            raise HTTPException(
                status_code=400,
                detail="Expand is only supported for flashcards and practice_questions",
            )

        # Check expand limit: max 1 additional generation
        if expand_count >= 1:
            logger.warning("expand_limit_exceeded", source_id=source_id, expand_count=expand_count)
            raise HTTPException(
                status_code=402,
                detail="You have reached the limit for generating additional content. Upgrade your plan to continue.",
            )

        logger.info("expanding_source", source_id=source_id, source_type=source_type, expand_count=expand_count)

        # ── 2. Parse existing content ─────────────────────────────────────────
        try:
            existing = json.loads(source["content"])
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=422, detail="Existing source content is not valid JSON")

        # ── 3. Collect lecture text from the originating documents ────────────
        doc_ids = source.get("document_ids") or []
        lecture_doc_ids = []
        past_year_doc_ids = []
        lecture_chunks: List[str] = []

        for doc_id in doc_ids:
            doc_result = (
                supabase.table("project_documents")
                .select("id, source_tag, flashcards, practice_questions, summary")
                .eq("id", doc_id)
                .eq("project_id", project_id)
                .execute()
            )
            if not doc_result.data:
                continue
            doc = doc_result.data[0]
            if doc.get("source_tag") == "past_year_paper":
                past_year_doc_ids.append(doc_id)
            else:
                lecture_doc_ids.append(doc_id)
                if doc.get("summary"):
                    lecture_chunks.append(doc["summary"])

        if not lecture_chunks:
            from src.features.utils import get_document_chunks_content
            for doc_id in lecture_doc_ids:
                lecture_chunks.extend(get_document_chunks_content(doc_id))

        lecture_text = "\n\n".join(lecture_chunks)[:20000]

        # ── 4. Check if there are remaining exam linkages ──────────────────────
        exam_linkages = None
        has_past_year = len(past_year_doc_ids) > 0
        if has_past_year and lecture_doc_ids:
            logger.info("rechecking_exam_linkages", lecture_docs=len(lecture_doc_ids), past_year_docs=len(past_year_doc_ids))
            exam_linkages = cross_reference_chunks(
                lecture_doc_ids=lecture_doc_ids,
                past_year_doc_ids=past_year_doc_ids,
            )

        # Extract already-covered exam topics from existing questions/cards
        covered_exam_topics = set()
        if source_type == "flashcards":
            for card in existing.get("flashcards", []):
                if card.get("is_past_year"):
                    covered_exam_topics.add(card.get("topic", "").lower())
        else:
            for q in existing.get("mcq", []) + existing.get("short_answer", []) + existing.get("paragraph", []):
                if q.get("is_past_year"):
                    covered_exam_topics.add(q.get("topic", "").lower())

        # Filter exam linkages to only uncovered topics
        remaining_exam_linkages = None
        if exam_linkages and exam_linkages.get("linkages"):
            remaining = []
            for link in exam_linkages["linkages"]:
                topic = link.get("lecture_excerpt", "").split("\n")[0].lower()[:50]
                if topic not in covered_exam_topics:
                    remaining.append(link)
            if remaining:
                remaining_exam_linkages = {
                    **exam_linkages,
                    "linkages": remaining,
                }
                logger.info("remaining_exam_linkages_found", count=len(remaining), previously_covered=len(covered_exam_topics))
            else:
                logger.info("no_remaining_exam_linkages", all_covered=len(covered_exam_topics))

        llm = openAI["features_llm"]

        # ── 4a. FLASHCARDS expand ─────────────────────────────────────────────
        if source_type == "flashcards":
            existing_cards = existing.get("flashcards", [])
            existing_fronts = "\n".join(
                f"- {c['front']}" for c in existing_cards
            )
            next_id = max((c["id"] for c in existing_cards), default=0) + 1

            if remaining_exam_linkages:
                linkage_block = format_linkages_for_prompt(remaining_exam_linkages)
                prompt = ChatPromptTemplate.from_messages([
                    ("user",
                     "You are an expert tutor creating additional exam-focused flashcards.\n\n"
                     "=== LECTURE NOTES ===\n{lecture_text}\n\n"
                     "=== REMAINING EXAM LINKAGES (new topics not yet covered) ===\n"
                     "{linkage_block}\n\n"
                     "=== ALREADY GENERATED (do NOT duplicate these) ===\n{existing_fronts}\n\n"
                     "Generate 15-20 NEW flashcards that prioritize the remaining exam-linked topics.\n\n"
                     "**Rules:**\n"
                     "- Focus on exam-linked content first\n"
                     "- Never duplicate or closely paraphrase an existing front\n"
                     "- Set is_past_year: true and exam_similarity to the linkage similarity for exam-linked cards\n"
                     "- If you cover non-linked topics, set is_past_year: false\n"
                     "- Mix difficulty: easy / medium / hard\n"
                     "- IDs must start from {next_id}\n\n"
                     "Return ONLY valid JSON, no other text:\n"
                     '{{\n'
                     '  "flashcards": [\n'
                     '    {{"id": {next_id}, "front": "...", "back": "...", "topic": "...", '
                     '"is_past_year": true, "exam_similarity": 0.85, "difficulty": "medium"}}\n'
                     '  ]\n'
                     '}}')
                ])

                response = llm.invoke(prompt.invoke({
                    "lecture_text": lecture_text,
                    "linkage_block": linkage_block,
                    "existing_fronts": existing_fronts,
                    "next_id": next_id,
                }))
                logger.info("exam_aware_flashcards_expansion", has_remaining_linkages=True)
            else:
                prompt = ChatPromptTemplate.from_messages([
                    ("user",
                     "You are an expert tutor creating additional study flashcards.\n\n"
                     "=== LECTURE CONTENT ===\n{lecture_text}\n\n"
                     "=== ALREADY GENERATED (do NOT duplicate these) ===\n{existing_fronts}\n\n"
                     "Generate 15-20 NEW flashcards that cover topics NOT already in the list above.\n\n"
                     "**Rules:**\n"
                     "- Never duplicate or closely paraphrase an existing front\n"
                     "- Cover remaining lecture topics; mix easy / medium / hard\n"
                     "- Set is_past_year: false and exam_similarity: 0.0 for all new cards\n"
                     "- IDs must start from {next_id}\n\n"
                     "Return ONLY valid JSON, no other text:\n"
                     '{{\n'
                     '  "flashcards": [\n'
                     '    {{"id": {next_id}, "front": "...", "back": "...", "topic": "...", '
                     '"is_past_year": false, "exam_similarity": 0.0, "difficulty": "medium"}}\n'
                     '  ]\n'
                     '}}')
                ])

                response = llm.invoke(prompt.invoke({
                    "lecture_text": lecture_text,
                    "existing_fronts": existing_fronts,
                    "next_id": next_id,
                }))
                logger.info("lecture_only_flashcards_expansion", reason="no_exam_linkages_remaining")

            new_data = json.loads(_clean_json(response.content))
            new_cards = new_data.get("flashcards", [])

            for i, card in enumerate(new_cards):
                card["id"] = next_id + i

            merged_cards = existing_cards + new_cards
            merged = {
                **existing,
                "flashcards": merged_cards,
                "total": len(merged_cards),
                "exam_relevant_count": sum(1 for c in merged_cards if c.get("is_past_year")),
            }
            added_count = len(new_cards)
            logger.info("flashcards_expanded", source_id=source_id,
                        previous=len(existing_cards), added=added_count, total=len(merged_cards))

        # ── 4b. PRACTICE QUESTIONS expand ─────────────────────────────────────
        else:
            existing_mcq = existing.get("mcq", [])
            existing_short = existing.get("short_answer", [])
            existing_para = existing.get("paragraph", [])

            existing_questions_summary = "\n".join(
                [f"[MCQ] {q['question']}" for q in existing_mcq] +
                [f"[Short] {q['question']}" for q in existing_short] +
                [f"[Para] {q['question']}" for q in existing_para]
            )

            next_mcq_id = max((q["id"] for q in existing_mcq), default=0) + 1
            next_short_id = max((q["id"] for q in existing_short), default=0) + 1
            next_para_id = max((q["id"] for q in existing_para), default=0) + 1

            if remaining_exam_linkages:
                linkage_block = format_linkages_for_prompt(remaining_exam_linkages)
                prompt = ChatPromptTemplate.from_messages([
                    ("user",
                     "You are an expert examiner creating additional exam-focused practice questions.\n\n"
                     "=== LECTURE CONTENT ===\n{lecture_text}\n\n"
                     "=== REMAINING EXAM LINKAGES (new topics not yet covered) ===\n"
                     "{linkage_block}\n\n"
                     "=== ALREADY GENERATED (do NOT duplicate these) ===\n{existing_questions}\n\n"
                     "Generate a fresh batch of NEW questions that prioritize the remaining exam-linked topics.\n\n"
                     "**MANDATORY MINIMUM COUNTS:**\n"
                     "- MCQ: minimum 8 (aim 10)\n"
                     "- Short Answer: minimum 4 (aim 6)\n"
                     "- Paragraph: minimum 2 (aim 3)\n\n"
                     "**Rules:**\n"
                     "- Focus on exam-linked content; never duplicate an existing question\n"
                     "- Set is_past_year: true and include exam_similarity for exam-linked questions\n"
                     "- If you cover non-linked topics, set is_past_year: false\n"
                     "- Mix difficulty: easy / medium / hard\n"
                     "- MCQ IDs start from {next_mcq_id}, short_answer from {next_short_id}, "
                     "paragraph from {next_para_id}\n"
                     "- MCQs: 4 options (A-D) + correct_answer + explanation\n"
                     "- short_answer: model_answer 2-3 sentences\n"
                     "- paragraph: marks (5-15) + detailed model_answer\n\n"
                     "Return ONLY valid JSON:\n"
                     '{{\n'
                     '  "mcq": [{{"id": {next_mcq_id}, "question": "...", '
                     '"options": ["A) ...","B) ...","C) ...","D) ..."], '
                     '"correct_answer": "A", "explanation": "...", "topic": "...", '
                     '"is_past_year": true, "difficulty": "medium"}}],\n'
                     '  "short_answer": [{{"id": {next_short_id}, "question": "...", '
                     '"model_answer": "...", "topic": "...", "is_past_year": true, "difficulty": "easy"}}],\n'
                     '  "paragraph": [{{"id": {next_para_id}, "question": "...", '
                     '"model_answer": "...", "marks": 10, "topic": "...", '
                     '"is_past_year": true, "difficulty": "hard"}}]\n'
                     '}}')
                ])

                response = llm.invoke(prompt.invoke({
                    "lecture_text": lecture_text,
                    "linkage_block": linkage_block,
                    "existing_questions": existing_questions_summary,
                    "next_mcq_id": next_mcq_id,
                    "next_short_id": next_short_id,
                    "next_para_id": next_para_id,
                }))
                logger.info("exam_aware_questions_expansion", has_remaining_linkages=True)
            else:
                prompt = ChatPromptTemplate.from_messages([
                    ("user",
                     "You are an expert examiner creating additional practice questions.\n\n"
                     "=== LECTURE CONTENT ===\n{lecture_text}\n\n"
                     "=== ALREADY GENERATED (do NOT duplicate these) ===\n{existing_questions}\n\n"
                     "Generate a fresh batch of NEW questions covering topics NOT already tested above.\n\n"
                     "**MANDATORY MINIMUM COUNTS:**\n"
                     "- MCQ: minimum 8 (aim 10)\n"
                     "- Short Answer: minimum 4 (aim 6)\n"
                     "- Paragraph: minimum 2 (aim 3)\n\n"
                     "**Rules:**\n"
                     "- Never duplicate or closely paraphrase an existing question\n"
                     "- Set is_past_year: false for ALL questions\n"
                     "- Mix difficulty: easy / medium / hard\n"
                     "- MCQ IDs start from {next_mcq_id}, short_answer from {next_short_id}, "
                     "paragraph from {next_para_id}\n"
                     "- MCQs: 4 options (A-D) + correct_answer + explanation\n"
                     "- short_answer: model_answer 2-3 sentences\n"
                     "- paragraph: marks (5-15) + detailed model_answer\n\n"
                     "Return ONLY valid JSON:\n"
                     '{{\n'
                     '  "mcq": [{{"id": {next_mcq_id}, "question": "...", '
                     '"options": ["A) ...","B) ...","C) ...","D) ..."], '
                     '"correct_answer": "A", "explanation": "...", "topic": "...", '
                     '"is_past_year": false, "difficulty": "medium"}}],\n'
                     '  "short_answer": [{{"id": {next_short_id}, "question": "...", '
                     '"model_answer": "...", "topic": "...", "is_past_year": false, "difficulty": "easy"}}],\n'
                     '  "paragraph": [{{"id": {next_para_id}, "question": "...", '
                     '"model_answer": "...", "marks": 10, "topic": "...", '
                     '"is_past_year": false, "difficulty": "hard"}}]\n'
                     '}}')
                ])

                response = llm.invoke(prompt.invoke({
                    "lecture_text": lecture_text,
                    "existing_questions": existing_questions_summary,
                    "next_mcq_id": next_mcq_id,
                    "next_short_id": next_short_id,
                    "next_para_id": next_para_id,
                }))
                logger.info("lecture_only_questions_expansion", reason="no_exam_linkages_remaining")

            new_data = json.loads(_clean_json(response.content))

            new_mcq = new_data.get("mcq", [])
            new_short = new_data.get("short_answer", [])
            new_para = new_data.get("paragraph", [])

            for i, q in enumerate(new_mcq):
                q["id"] = next_mcq_id + i
            for i, q in enumerate(new_short):
                q["id"] = next_short_id + i
            for i, q in enumerate(new_para):
                q["id"] = next_para_id + i

            merged_mcq = existing_mcq + new_mcq
            merged_short = existing_short + new_short
            merged_para = existing_para + new_para
            all_questions = merged_mcq + merged_short + merged_para

            merged = {
                **existing,
                "mcq": merged_mcq,
                "short_answer": merged_short,
                "paragraph": merged_para,
                "total": len(all_questions),
                "exam_relevant_count": sum(1 for q in all_questions if q.get("is_past_year")),
                "breakdown": {
                    "mcq": len(merged_mcq),
                    "short_answer": len(merged_short),
                    "paragraph": len(merged_para),
                },
            }
            added_count = len(new_mcq) + len(new_short) + len(new_para)
            logger.info("practice_questions_expanded", source_id=source_id,
                        added=added_count, total=len(all_questions))

        # ── 5. Persist back to DB and increment expand_count ──────────────────
        merged_json = json.dumps(merged, indent=2)
        update_result = (
            supabase.table("generated_sources")
            .update({"content": merged_json, "expand_count": expand_count + 1})
            .eq("id", source_id)
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )
        if not update_result.data:
            raise HTTPException(status_code=422, detail="Failed to save expanded content")

        logger.info("source_expanded_and_saved", source_id=source_id, added=added_count, new_expand_count=expand_count + 1)
        return {
            "message": f"Added {added_count} new items",
            "added_count": added_count,
            "content": merged_json,
            "expand_count": expand_count + 1,
            "data": update_result.data[0],
        }

    except HTTPException as e:
        raise e
    except json.JSONDecodeError as e:
        logger.error("expand_json_parse_error", source_id=source_id, error=str(e))
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {str(e)}")
    except Exception as e:
        logger.error("expand_source_error", source_id=source_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error expanding source: {str(e)}")


# =============================================================================
# QUIZ ANSWER EVALUATION (stateless - no DB write)
# =============================================================================

@router.post("/{project_id}/quiz/evaluate")
async def evaluate_quiz_answer(
    project_id: str,
    request: QuizEvaluateRequest,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """
    Grade a student's written answer (short answer or paragraph) against the model
    answer using the LLM. Returns marks + structured feedback. Nothing is persisted.
    """
    set_project_id(project_id)
    set_user_id(current_user_clerk_id)
    try:
        logger.info("evaluating_quiz_answer", question_type=request.question_type, max_marks=request.max_marks)

        max_marks = request.max_marks if request.max_marks and request.max_marks > 0 else 10

        if not request.user_answer.strip():
            return {
                "awarded_marks": 0,
                "max_marks": max_marks,
                "verdict": "incorrect",
                "feedback": "No answer was provided. Try writing what you know, even partially.",
                "strengths": [],
                "improvements": ["Attempt the question - even a partial answer can earn marks."],
            }

        llm = openAI["mini_llm"].with_structured_output(QuizEvaluation)

        prompt = f"""You are a fair but rigorous university exam grader.
Grade the STUDENT ANSWER against the MODEL ANSWER for the question below.

QUESTION:
{request.question}

MODEL ANSWER:
{request.model_answer}

STUDENT ANSWER:
{request.user_answer}

MAXIMUM MARKS: {max_marks}

Grading rules:
- Judge on conceptual correctness and coverage of key points, NOT exact wording.
- Reward partial understanding proportionally.
- Award marks from 0 to {max_marks} (half marks like 0.5 are allowed).
- verdict: "correct" if the answer earns >= 80% of max marks,
  "partial" if between 30% and 80%, "incorrect" if below 30%.
- Keep feedback to 2-3 sentences: encouraging but honest.
- Give up to 3 concise strengths and up to 3 concise improvements.
"""

        result: QuizEvaluation = llm.invoke(prompt)

        awarded = max(0.0, min(float(result.awarded_marks), float(max_marks)))

        logger.info("quiz_answer_evaluated", awarded=awarded, max_marks=max_marks, verdict=result.verdict)
        return {
            "awarded_marks": awarded,
            "max_marks": max_marks,
            "verdict": result.verdict,
            "feedback": result.feedback,
            "strengths": result.strengths[:3],
            "improvements": result.improvements[:3],
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("quiz_evaluation_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error evaluating answer: {str(e)}")


# =============================================================================
# GET / DELETE
# =============================================================================

@router.get("/{project_id}/documents/{document_id}/features")
async def get_document_features(
    project_id: str,
    document_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """Get all generated features for a single document."""
    set_project_id(project_id)
    set_user_id(current_user_clerk_id)
    try:
        logger.info("fetching_document_features", document_id=document_id)
        result = (
            supabase.table("project_documents")
            .select("id, filename, summary, mind_map, flashcards, practice_questions, features_status, source_tag")
            .eq("id", document_id)
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )
        if not result.data:
            logger.warning("document_not_found_for_features", document_id=document_id)
            raise HTTPException(status_code=404, detail="Document not found")

        logger.info("document_features_retrieved", document_id=document_id)
        return {"message": "Document features retrieved successfully", "data": result.data[0]}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("document_features_retrieval_error", document_id=document_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.get("/{project_id}/sources")
async def get_generated_sources(
    project_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """Get all generated sources for a project."""
    set_project_id(project_id)
    set_user_id(current_user_clerk_id)
    try:
        logger.info("fetching_generated_sources")
        result = (
            supabase.table("generated_sources")
            .select("*")
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .order("created_at", desc=True)
            .execute()
        )
        logger.info("generated_sources_retrieved", source_count=len(result.data or []))
        return {"message": "Generated sources retrieved successfully", "data": result.data or []}

    except Exception as e:
        logger.error("generated_sources_retrieval_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.delete("/{project_id}/sources/{source_id}")
async def delete_generated_source(
    project_id: str,
    source_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """Delete a generated source."""
    set_project_id(project_id)
    set_user_id(current_user_clerk_id)
    try:
        logger.info("deleting_generated_source", source_id=source_id)
        result = (
            supabase.table("generated_sources")
            .delete()
            .eq("id", source_id)
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )
        if not result.data:
            logger.warning("generated_source_not_found", source_id=source_id)
            raise HTTPException(status_code=404, detail="Source not found")

        logger.info("generated_source_deleted_successfully", source_id=source_id)
        return {"message": "Generated source deleted successfully", "data": result.data[0]}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("generated_source_deletion_error", source_id=source_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
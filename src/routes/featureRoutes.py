"""
Feature Generation Routes.

Endpoints:
  POST /api/projects/{project_id}/features/generate
  POST /api/projects/{project_id}/features/merge
  GET  /api/projects/{project_id}/documents/{document_id}/features
  GET  /api/projects/{project_id}/sources
  DELETE /api/projects/{project_id}/sources/{source_id}
  PUT  /api/projects/{project_id}/files/{document_id}/tag
"""

from typing import List
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Depends
from src.services.supabase import supabase
from src.services.clerkAuth import get_current_user_clerk_id
from src.features.generate import generate_single_feature_for_document, generate_exam_aware_feature
from src.features.utils import merge_contents, generate_title
from src.features.mind_map import generate_mind_map


router = APIRouter(tags=["featureRoutes"])


# =============================================================================
# REQUEST MODELS
# =============================================================================

class FeatureGenerateRequest(BaseModel):
    doc_ids: List[str] = Field(..., description="List of document IDs")
    feature_type: str = Field(..., description="Feature type: summary, flashcards, practice_questions, mind_map, faq, study_guide, briefing_doc")


class FeatureMergeRequest(BaseModel):
    doc_ids: List[str] = Field(..., description="List of document IDs to merge features from")
    source_type: str = Field(..., description="Source type")


class DocumentTagRequest(BaseModel):
    source_tag: str = Field(..., description="Either 'lecture_notes' or 'past_year_paper'")


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
    try:
        valid_tags = ["lecture_notes", "past_year_paper"]
        if request.source_tag not in valid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid source_tag. Must be one of: {valid_tags}"
            )

        result = (
            supabase.table("project_documents")
            .update({"source_tag": request.source_tag})
            .eq("id", document_id)
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=404, detail="Document not found")

        return {
            "message": "Document tagged successfully",
            "data": result.data[0],
        }

    except HTTPException as e:
        raise e
    except Exception as e:
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
    """
    Generate a specific feature for the given documents.
    Uses RAG cross-referencing when both lecture notes and past year papers are selected.
    """
    try:
        feature_type = request.feature_type
        valid_types = ["summary", "faq", "study_guide", "briefing_doc", "mind_map", "flashcards", "practice_questions"]

        if feature_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid feature_type. Must be one of: {valid_types}"
            )

        if not request.doc_ids:
            raise HTTPException(status_code=400, detail="doc_ids is required")

        # For exam-aware features (flashcards, practice_questions, summary),
        # use the cross-referencing pipeline
        exam_aware_types = ["flashcards", "practice_questions", "summary"]

        if feature_type in exam_aware_types:
            # Check if any selected doc is a past year paper
            has_past_year = False
            for doc_id in request.doc_ids:
                doc_result = (
                    supabase.table("project_documents")
                    .select("source_tag")
                    .eq("id", doc_id)
                    .execute()
                )
                if doc_result.data and doc_result.data[0].get("source_tag") == "past_year_paper":
                    has_past_year = True
                    break

            if has_past_year:
                # Use exam-aware generation with RAG cross-referencing
                result = generate_exam_aware_feature(
                    project_id=project_id,
                    doc_ids=request.doc_ids,
                    feature_type=feature_type,
                )

                # Store result on the first lecture notes document
                for doc_id in request.doc_ids:
                    doc_result = (
                        supabase.table("project_documents")
                        .select("source_tag")
                        .eq("id", doc_id)
                        .execute()
                    )
                    if doc_result.data and doc_result.data[0].get("source_tag") != "past_year_paper":
                        supabase.table("project_documents").update(
                            {feature_type: result}
                        ).eq("id", doc_id).execute()
                        break

                return {
                    "message": "Feature generation complete (exam-aware with RAG cross-referencing)",
                    "status": "ready_to_generate_source",
                    "feature_type": feature_type,
                    "cross_referenced": True,
                    "generated_count": 1,
                    "total_docs": len(request.doc_ids),
                }

        # Standard per-document generation (no past year paper)
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
                continue

            existing_value = doc_result.data[0].get(feature_type)

            if not existing_value:
                generate_single_feature_for_document(doc_id, feature_type)
                generated_count += 1

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
        raise HTTPException(
            status_code=500,
            detail=f"Error generating features: {str(e)}"
        )


# =============================================================================
# MERGE INTO GENERATED SOURCE
# =============================================================================

@router.post("/{project_id}/features/merge")
async def merge_features(
    project_id: str,
    request: FeatureMergeRequest,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """
    Merge features from documents into a generated_source.
    For flashcards and practice_questions, the content is JSON.
    """
    try:
        source_type = request.source_type
        valid_types = ["summary", "faq", "study_guide", "briefing_doc", "mind_map", "flashcards", "practice_questions"]

        if source_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid source_type. Must be one of: {valid_types}"
            )

        if not request.doc_ids:
            raise HTTPException(status_code=400, detail="doc_ids is required")

        # For mind_map, use study_guide content
        db_column = "study_guide" if source_type == "mind_map" else source_type

        # Fetch feature content from each document
        contents = []
        for doc_id in request.doc_ids:
            doc_result = (
                supabase.table("project_documents")
                .select(f"id, filename, {db_column}")
                .eq("id", doc_id)
                .eq("project_id", project_id)
                .eq("clerk_id", current_user_clerk_id)
                .execute()
            )

            if doc_result.data and doc_result.data[0].get(db_column):
                contents.append({
                    "title": doc_result.data[0].get("filename"),
                    "content": doc_result.data[0].get(db_column),
                })

        if not contents:
            raise HTTPException(
                status_code=400,
                detail=f"No {source_type} content found. Generate features first."
            )

        # For JSON-based features (flashcards, practice_questions), use directly
        if source_type in ["flashcards", "practice_questions"]:
            final_content = contents[0]["content"]
            title_input = contents[0].get("title", "Practice Material")[:200]
        elif source_type == "mind_map":
            if len(contents) == 1:
                study_guide_text = contents[0]["content"]
            else:
                study_guide_text = merge_contents(contents, "study_guide")
            final_content = generate_mind_map(study_guide_text)
            title_input = study_guide_text[:2000]
        elif len(contents) == 1:
            final_content = contents[0]["content"]
            title_input = final_content[:2000]
        else:
            final_content = merge_contents(contents, source_type)
            title_input = final_content[:2000]

        # Generate title
        title = generate_title(title_input)

        # Store in generated_sources
        source_data = {
            "project_id": project_id,
            "clerk_id": current_user_clerk_id,
            "title": title,
            "source_type": source_type,
            "content": final_content,
            "document_ids": request.doc_ids,
            "total_sources": len(request.doc_ids),
        }

        result = supabase.table("generated_sources").insert(source_data).execute()

        if not result.data:
            raise HTTPException(status_code=422, detail="Failed to create generated source")

        return {
            "message": f"Generated {source_type} source created successfully",
            "data": result.data[0],
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error merging features: {str(e)}"
        )


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
    try:
        result = (
            supabase.table("project_documents")
            .select("id, filename, summary, faq, study_guide, briefing_doc, mind_map, flashcards, practice_questions, features_status, source_tag")
            .eq("id", document_id)
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=404, detail="Document not found")

        return {
            "message": "Document features retrieved successfully",
            "data": result.data[0],
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.get("/{project_id}/sources")
async def get_generated_sources(
    project_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """Get all generated sources for a project."""
    try:
        result = (
            supabase.table("generated_sources")
            .select("*")
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .order("created_at", desc=True)
            .execute()
        )

        return {
            "message": "Generated sources retrieved successfully",
            "data": result.data or [],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.delete("/{project_id}/sources/{source_id}")
async def delete_generated_source(
    project_id: str,
    source_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    """Delete a generated source."""
    try:
        result = (
            supabase.table("generated_sources")
            .delete()
            .eq("id", source_id)
            .eq("project_id", project_id)
            .eq("clerk_id", current_user_clerk_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=404, detail="Source not found")

        return {
            "message": "Generated source deleted successfully",
            "data": result.data[0],
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
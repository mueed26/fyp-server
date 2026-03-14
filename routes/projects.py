from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import supabase
from auth import get_current_user

router = APIRouter(
    tags=["projects"]
)

"""
`/api/projects`

List all projects: GET `/api/projects`
Create a new project: POST `/api/projects`
Delete a project: DELETE `/api/projects/{project_id}`
Get a project: GET `/api/projects/{project_id}`
Get project chats: GET `/api/projects/{project_id}/chats`
Get project settings: GET `/api/projects/{project_id}/settings`
Update project settings: PUT `/api/projects/{project_id}/settings`
"""

class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectSettings(BaseModel):
    embedding_model: str
    rag_strategy: str
    agent_type: str
    chunks_per_search: int
    final_context_size: int
    similarity_threshold: float
    number_of_queries: int
    reranking_enabled: bool
    reranking_model: str
    vector_weight: float
    keyword_weight: float


@router.get("/api/projects") 
def get_projects(clerk_id: str = Depends(get_current_user)): 
    """
    Retrieve all projects for the authenticated user
    """
    try:
        result = supabase.table('projects').select('*').eq('clerk_id', clerk_id).execute()
 
        return { 
            "success": True,
            "message": "Projects retrieved successfully",
            "data": result.data
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get projects: {str(e)}")


@router.post("/api/projects") 
def create_project(project: ProjectCreate, clerk_id: str = Depends(get_current_user)):
    """
    Create a new project with default settings
    
    Logic Flow:
    1. Insert new project into database
    2. Create default project settings
    3. If settings creation fails, rollback project creation
    4. Return created project
    """
    try:
        # Step 1: Insert new project into database
        project_result = supabase.table("projects").insert({
            "name": project.name, 
            "description": project.description,
            "clerk_id": clerk_id
        }).execute()

        if not project_result.data:
            raise HTTPException(
                status_code=422, 
                detail="Failed to create project - invalid data provided"
            )

        created_project = project_result.data[0]
        project_id = created_project["id"]

        # Step 2: Create default settings for the project 
        settings_result = supabase.table("project_settings").insert({
            "project_id": project_id, 
            "embedding_model": "text-embedding-3-large",
            "rag_strategy": "basic",
            "agent_type": "agentic",
            "chunks_per_search": 10,
            "final_context_size": 5,
            "similarity_threshold": 0.3,
            "number_of_queries": 5,
            "reranking_enabled": True,
            "reranking_model": "rerank-english-v3.0",
            "vector_weight": 0.7,
            "keyword_weight": 0.3,
        }).execute()

        if not settings_result.data:
            # Step 3: Rollback - Delete the project if settings creation fails
            supabase.table("projects").delete().eq("id", project_id).execute()
            raise HTTPException(
                status_code=422, 
                detail="Failed to create project settings - project creation rolled back"
            )

        return {
            "success": True,
            "message": "Project created successfully", 
            "data": created_project 
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"An internal server error occurred while creating project: {str(e)}"
        )


@router.delete("/api/projects/{project_id}")
def delete_project(
    project_id: str, 
    clerk_id: str = Depends(get_current_user)
):
    """
    Delete a project and all related data
    
    Logic Flow:
    1. Verify project exists and belongs to user
    2. Delete project (CASCADE handles all related data: settings, documents, chunks, chats, messages)
    """
    try:
        # Step 1: Verify project exists and belongs to user 
        project_result = supabase.table("projects").select("*").eq("id", project_id).eq("clerk_id", clerk_id).execute()

        if not project_result.data: 
            raise HTTPException(
                status_code=404, 
                detail="Project not found or you don't have permission to delete it"
            )

        # Step 2: Delete project (CASCADE handles all related data)
        deleted_result = supabase.table("projects").delete().eq("id", project_id).eq("clerk_id", clerk_id).execute()

        if not deleted_result.data: 
            raise HTTPException(
                status_code=404, 
                detail="Failed to delete project - project not found"
            )

        return {
            "success": True,
            "message": "Project deleted successfully", 
            "data": deleted_result.data[0]  
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"An internal server error occurred while deleting project: {str(e)}"
        )



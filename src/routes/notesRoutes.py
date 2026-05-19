from fastapi import APIRouter, HTTPException, Depends
from src.services.supabase import supabase
from src.services.clerkAuth import get_current_user_clerk_id
from pydantic import BaseModel
from src.services.llm import openAI
from langchain_core.prompts import ChatPromptTemplate

router = APIRouter()


class NoteCreate(BaseModel):
    text: str


class NoteUpdate(BaseModel):
    text: str


class NoteAskRequest(BaseModel):
    questions: list[str]


# Create note
@router.post("/{project_id}/notes")
async def create_note(
    project_id: str,
    note: NoteCreate,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Create a new note in a project"""
    try:
        result = supabase.table("notes").insert({
            "project_id": project_id,
            "clerk_id": current_user_clerk_id,
            "text": note.text
        }).execute()
        return result.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Get all notes for a project
@router.get("/{project_id}/notes")
async def get_project_notes(
    project_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Get all notes for a project"""
    try:
        result = supabase.table("notes").select("*").eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).order("created_at", desc=True).execute()
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Get single note
@router.get("/{project_id}/notes/{note_id}")
async def get_note(
    project_id: str,
    note_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Get a single note"""
    try:
        result = supabase.table("notes").select("*").eq(
            "id", note_id
        ).eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).single().execute()
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Update note
@router.put("/{project_id}/notes/{note_id}")
async def update_note(
    project_id: str,
    note_id: str,
    note: NoteUpdate,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Update a note"""
    try:
        result = supabase.table("notes").update({
            "text": note.text
        }).eq(
            "id", note_id
        ).eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).execute()
        return result.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Delete note
@router.delete("/{project_id}/notes/{note_id}")
async def delete_note(
    project_id: str,
    note_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Delete a note"""
    try:
        supabase.table("notes").delete().eq(
            "id", note_id
        ).eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).execute()
        return {"message": "Note deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Ask AI about notes
@router.post("/{project_id}/notes/ask")
async def ask_ai_about_notes(
    project_id: str,
    body: NoteAskRequest,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Ask AI a question about notes in a project"""
    try:
        notes_result = supabase.table("notes").select("text, created_at, updated_at").eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).execute()

        notes = notes_result.data
        if not notes:
            return {"response": "You don't have any notes yet."}

        formatted_notes = "\n\n".join([
            f"Text: {note['text']}\nCreated: {note['created_at']}\nUpdated: {note['updated_at']}"
            for note in notes
        ])

        conversation = "\n".join([
            f"Q{i+1}: {q}" for i, q in enumerate(body.questions)
        ])

        mini_llm = openAI["mini_llm"]

        prompt = ChatPromptTemplate.from_messages([
            ("system",
             f"""You are a helpful assistant that answers questions about a user's notes.
Assume all questions are related to the user's notes.
Make sure that your answers are not too verbose and you speak succinctly.
Your responses MUST be formatted in clean, valid HTML with proper structure.
Use tags like <p>, <strong>, <em>, <ul>, <ol>, <li>, <h1> to <h6>, and <br> when appropriate.
Do NOT wrap the entire response in a single <p> tag unless it's a single paragraph.
Avoid inline styles, JavaScript, or custom attributes.

Here are the user's notes:
{formatted_notes}"""),
            ("human", "{conversation}")
        ])

        response = mini_llm.invoke(prompt.invoke({"conversation": conversation}))

        return {"response": str(response.content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
from fastapi import APIRouter, HTTPException, Depends
from src.services.supabase import supabase
from src.services.clerkAuth import get_current_user_clerk_id
from pydantic import BaseModel
from src.services.llm import openAI
from langchain_core.prompts import ChatPromptTemplate
from src.config.logging import get_logger, set_project_id, set_user_id  # ADDED

logger = get_logger(__name__)  # ADDED

router = APIRouter()


class NoteCreate(BaseModel):
    text: str


class NoteUpdate(BaseModel):
    text: str


class NoteAskRequest(BaseModel):
    question: str  # Single new question only — history is loaded from DB


class NoteClearHistoryRequest(BaseModel):
    note_id: str


# ─────────────────────────────────────────────
# NOTES CRUD
# ─────────────────────────────────────────────

@router.post("/{project_id}/notes")
async def create_note(
    project_id: str,
    note: NoteCreate,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Create a new note in a project"""
    set_project_id(project_id)          # ADDED
    set_user_id(current_user_clerk_id)  # ADDED
    try:
        logger.info("creating_note", text_length=len(note.text))  # ADDED
        result = supabase.table("notes").insert({
            "project_id": project_id,
            "clerk_id": current_user_clerk_id,
            "text": note.text
        }).execute()

        note_id = result.data[0].get("id")
        logger.info("note_created_successfully", note_id=note_id)  # ADDED
        return result.data[0]

    except Exception as e:
        logger.error("note_creation_error", error=str(e), exc_info=True)  # ADDED
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}/notes")
async def get_project_notes(
    project_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Get all notes for a project"""
    set_project_id(project_id)          # ADDED
    set_user_id(current_user_clerk_id)  # ADDED
    try:
        logger.info("fetching_project_notes")  # ADDED
        result = supabase.table("notes").select("*").eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).order("created_at", desc=True).execute()

        logger.info("project_notes_retrieved", note_count=len(result.data or []))  # ADDED
        return result.data

    except Exception as e:
        logger.error("project_notes_retrieval_error", error=str(e), exc_info=True)  # ADDED
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}/notes/{note_id}")
async def get_note(
    project_id: str,
    note_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Get a single note"""
    set_project_id(project_id)          # ADDED
    set_user_id(current_user_clerk_id)  # ADDED
    try:
        logger.info("fetching_note", note_id=note_id)  # ADDED
        result = supabase.table("notes").select("*").eq(
            "id", note_id
        ).eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).single().execute()

        logger.info("note_retrieved", note_id=note_id)  # ADDED
        return result.data

    except Exception as e:
        logger.error("note_retrieval_error", note_id=note_id, error=str(e), exc_info=True)  # ADDED
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{project_id}/notes/{note_id}")
async def update_note(
    project_id: str,
    note_id: str,
    note: NoteUpdate,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Update a note"""
    set_project_id(project_id)          # ADDED
    set_user_id(current_user_clerk_id)  # ADDED
    try:
        logger.info("updating_note", note_id=note_id, text_length=len(note.text))  # ADDED
        result = supabase.table("notes").update({
            "text": note.text
        }).eq(
            "id", note_id
        ).eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).execute()

        logger.info("note_updated_successfully", note_id=note_id)  # ADDED
        return result.data[0]

    except Exception as e:
        logger.error("note_update_error", note_id=note_id, error=str(e), exc_info=True)  # ADDED
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{project_id}/notes/{note_id}")
async def delete_note(
    project_id: str,
    note_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Delete a note (cascades to note_conversations via FK)"""
    set_project_id(project_id)          # ADDED
    set_user_id(current_user_clerk_id)  # ADDED
    try:
        logger.info("deleting_note", note_id=note_id)  # ADDED
        supabase.table("notes").delete().eq(
            "id", note_id
        ).eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).execute()

        logger.info("note_deleted_successfully", note_id=note_id)  # ADDED
        return {"message": "Note deleted successfully"}

    except Exception as e:
        logger.error("note_deletion_error", note_id=note_id, error=str(e), exc_info=True)  # ADDED
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# CONVERSATION HISTORY
# ─────────────────────────────────────────────

@router.get("/{project_id}/notes/{note_id}/conversation")
async def get_note_conversation(
    project_id: str,
    note_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """
    Load persisted conversation history for a note.
    Called by the frontend when the user selects a note.
    """
    set_project_id(project_id)          # ADDED
    set_user_id(current_user_clerk_id)  # ADDED
    try:
        logger.info("fetching_note_conversation", note_id=note_id)  # ADDED

        # Verify note ownership
        note_check = supabase.table("notes").select("id").eq(
            "id", note_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).execute()

        if not note_check.data:
            logger.warning("note_not_found_for_conversation", note_id=note_id)  # ADDED
            raise HTTPException(status_code=404, detail="Note not found")

        result = supabase.table("note_conversations").select(
            "id, role, content, created_at"
        ).eq(
            "note_id", note_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).order("created_at", desc=False).execute()

        logger.info("note_conversation_retrieved", note_id=note_id, message_count=len(result.data or []))  # ADDED
        return result.data or []

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("note_conversation_retrieval_error", note_id=note_id, error=str(e), exc_info=True)  # ADDED
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{project_id}/notes/{note_id}/conversation")
async def clear_note_conversation(
    project_id: str,
    note_id: str,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """Clear all conversation history for a note"""
    set_project_id(project_id)          # ADDED
    set_user_id(current_user_clerk_id)  # ADDED
    try:
        logger.info("clearing_note_conversation", note_id=note_id)  # ADDED
        supabase.table("note_conversations").delete().eq(
            "note_id", note_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).execute()

        logger.info("note_conversation_cleared", note_id=note_id)  # ADDED
        return {"message": "Conversation cleared"}

    except Exception as e:
        logger.error("note_conversation_clear_error", note_id=note_id, error=str(e), exc_info=True)  # ADDED
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# AI ASK  (with persistent history)
# ─────────────────────────────────────────────

@router.post("/{project_id}/notes/{note_id}/ask")
async def ask_ai_about_note(
    project_id: str,
    note_id: str,
    body: NoteAskRequest,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id)
):
    """
    Ask AI a question about a specific note.

    Flow:
    1. Verify note ownership and fetch note text
    2. Load full conversation history from DB for this note
    3. Persist the new user message
    4. Build LLM prompt with note content + history + new question
    5. Get AI response
    6. Persist the assistant reply
    7. Return the assistant reply
    """
    set_project_id(project_id)          # ADDED
    set_user_id(current_user_clerk_id)  # ADDED
    try:
        logger.info("ask_ai_about_note", note_id=note_id)  # ADDED

        # ── 1. Fetch the note ──────────────────────────────────────────────
        note_result = supabase.table("notes").select(
            "id, text, project_id"
        ).eq(
            "id", note_id
        ).eq(
            "project_id", project_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).execute()

        if not note_result.data:
            logger.warning("note_not_found_for_ask", note_id=note_id)  # ADDED
            raise HTTPException(status_code=404, detail="Note not found")

        note = note_result.data[0]
        note_text = note["text"]

        if not note_text.strip():
            logger.info("note_is_empty", note_id=note_id)  # ADDED
            return {"response": "This note is empty. Add some content first."}

        # ── 2. Load conversation history from DB ───────────────────────────
        history_result = supabase.table("note_conversations").select(
            "role, content"
        ).eq(
            "note_id", note_id
        ).eq(
            "clerk_id", current_user_clerk_id
        ).order("created_at", desc=False).execute()

        db_history = history_result.data or []
        logger.info("conversation_history_loaded", note_id=note_id, history_length=len(db_history))  # ADDED

        # ── 3. Persist the new user message ───────────────────────────────
        supabase.table("note_conversations").insert({
            "note_id": note_id,
            "project_id": project_id,
            "clerk_id": current_user_clerk_id,
            "role": "user",
            "content": body.question,
        }).execute()

        # ── 4. Build prompt ────────────────────────────────────────────────
        # Format prior turns so the LLM sees the conversation context
        history_text = ""
        if db_history:
            turns = []
            for msg in db_history:
                label = "User" if msg["role"] == "user" else "Assistant"
                turns.append(f"{label}: {msg['content']}")
            history_text = "\n\n".join(turns)

        mini_llm = openAI["mini_llm"]

        system_content = f"""You are a helpful assistant that answers questions about a user's note.
Assume all questions are related to the note content shown below.
Make sure your answers are concise and directly useful.
Your responses MUST be formatted in clean, valid HTML with proper structure.
Use tags like <p>, <strong>, <em>, <ul>, <ol>, <li>, <h1> to <h6>, and <br> when appropriate.
Do NOT wrap the entire response in a single <p> tag unless it is a single paragraph.
Avoid inline styles, JavaScript, or custom attributes.

Here is the note the user is asking about:
\"\"\"
{note_text}
\"\"\""""

        if history_text:
            system_content += f"""

Here is the conversation history so far (use this for context):
{history_text}"""

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_content),
            ("human", "{question}")
        ])

        logger.info("invoking_llm_for_note", note_id=note_id, has_history=bool(db_history))  # ADDED
        response = mini_llm.invoke(prompt.invoke({"question": body.question}))
        ai_reply = str(response.content)
        logger.info("llm_response_received", note_id=note_id, response_length=len(ai_reply))  # ADDED

        # ── 5. Persist assistant reply ─────────────────────────────────────
        supabase.table("note_conversations").insert({
            "note_id": note_id,
            "project_id": project_id,
            "clerk_id": current_user_clerk_id,
            "role": "assistant",
            "content": ai_reply,
        }).execute()

        logger.info("note_ask_completed_successfully", note_id=note_id)  # ADDED
        return {"response": ai_reply}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("note_ask_error", note_id=note_id, error=str(e), exc_info=True)  # ADDED
        raise HTTPException(status_code=500, detail=str(e))
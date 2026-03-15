from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import supabase
from auth import get_current_user

router = APIRouter(
    tags=["chats"]
)  

class ChatCreate(BaseModel):
    title: str
    project_id: str


@router.post("/api/chats")
async def create_chat(
    chat: ChatCreate, 
    clerk_id: str = Depends(get_current_user)
):
    try:
        result = supabase.table("chats").insert({
            "title": chat.title, 
            "project_id": chat.project_id, 
            "clerk_id": clerk_id
        }).execute()

        return {
            "message": "Chat created successfully", 
            "data": result.data[0]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail = f"Failed to create chat: {str(e)}")


@router.delete("/api/chats/{chat_id}")
async def delete_chat(
    chat_id: str, 
    clerk_id: str = Depends(get_current_user)
):
    try:
        deleted_result = supabase.table("chats").delete().eq("id", chat_id).eq("clerk_id", clerk_id).execute()

        if not deleted_result.data: 
            raise HTTPException(status_code=404, detail="Chat not found or access denied")

        return {
            "message": "Chat Deleted Successfully", 
            "data": deleted_result.data[0]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail = f"Failed to delete chat: {str(e)}")
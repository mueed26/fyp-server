from typing import Any
from fastapi import APIRouter, HTTPException
from database import supabase

router = APIRouter(
    tags=["users"]
)

"""
`/api/user`

Create user from Clerk webhook: POST `/api/users/webhook`
"""


@router.post("/api/users/webhook")
async def create_user_from_clerk_webhook(clerk_webhook_data: dict):
    """
    Handle Clerk user.created webhook event
    
    Logic Flow:
    1. Validate webhook payload structure
    2. Check event type (only process user.created)
    3. Extract and validate clerk_id
    4. Check for duplicate users (webhooks can be retried)
    5. Create new user in database
    6. Return success response
    """
    try:
        # Step 1: Validate webhook payload structure
        if not isinstance(clerk_webhook_data, dict):
            raise HTTPException(
                status_code=400, 
                detail="Invalid webhook payload format"
            )
        
        # Step 2: Check event type
        event_type = clerk_webhook_data.get("type")
        if event_type != "user.created":
            # Return success for other events (don't retry)
            return {
                "success": True,
                "message": f"Event type '{event_type}' ignored"
            }
        
        # Step 3: Extract and validate user data
        user_data = clerk_webhook_data.get("data")
        if not user_data or not isinstance(user_data, dict):
            raise HTTPException(
                status_code=400,
                detail="Missing or invalid user data in webhook payload"
            )
        
        # Step 4: Extract and validate clerk_id
        clerk_id = user_data.get("id")
        if not clerk_id or not isinstance(clerk_id, str):
            raise HTTPException(
                status_code=400, 
                detail="Missing or invalid clerk_id in user data"
            )
        
        # Step 5: Check if user already exists (webhook idempotency)
        existing_user = (
            supabase.table("users")
            .select("clerk_id")
            .eq("clerk_id", clerk_id)
            .execute()
        )
        
        if existing_user.data:
            # User already exists - return success (don't retry webhook)
            return {
                "success": True,
                "message": "User already exists",
                "clerk_id": clerk_id
            }
        
        # Step 6: Create new user in database
        result = supabase.table("users").insert({
            "clerk_id": clerk_id
        }).execute()
        
        # Step 7: Verify insertion was successful
        if not result.data:
            raise HTTPException(
                status_code=500, 
                detail="Failed to create user in database"
            )
        
        return {
            "success": True,
            "message": "User created successfully",
            "user": result.data[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        # Only catch unexpected exceptions (database errors, network errors, etc.)
        raise HTTPException(
            status_code=500,
            detail=f"An internal server error occurred while processing webhook: {str(e)}"
        )
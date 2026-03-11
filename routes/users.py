from fastapi import FastAPI
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel



from database import supabase
import os


#give users tag for python routes
router=APIRouter(
    tags=["users"]
)

#api endpint 
#clerk sending data to us 
@router.post("/create-user")
async def clerk_webhook(webhook_data:dict):
    try:
        event_type=webhook_data.get("type")
        if event_type=="user.created":
            #extract data and clerk id
            user_data=webhook_data.get("data",{})
            clerk_id=user_data.get("id")
        if not clerk_id:
            raise HTTPException(status_code=400,detail="No user ID in webhook") 
        
        #create user id
        result=supabase.table('users').insert({
            "clerk_id":clerk_id
        }).execute()

        return{
            "message":"user created sucessfully",
            "data":result.data[0]
        }
    except Exception as e:
         raise HTTPException(status_code=500,detail=f"webhook processing failed: {str(e)}")
# we downloaded supabase locally 
#npx supabase start will spin up docker conatiner with postgresql,auth apiis and etc 
#pulls all images from dockerhub and start docker
#it will create the supabase backened service 


#this is the fats api application 

from fastapi import FastAPI, HTTPException
from database import supabase
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client,Client #to set up the client 
from dotenv import load_dotenv
import os

load_dotenv()

from routers import users





# Create FastAPI app
app = FastAPI(
    title="AI_study_companion",
    description="Backend for AI Study Companion",
    version="1.0.0",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"], #all the methods get put path etc and all 
    allow_headers=["*"],
)


app.include_router(users.router)

# Health check endpoints
@app.get("/")
async def root():
    return {"message": "FYP app is running!"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "1.0.0"}


@app.get("/posts")
async def get_all_posts():
    """get all blog posts"""
    try:
        result=supabase.table("posts").select("*").order("created_at",desc=True).execute()
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500,detail=str(e))



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
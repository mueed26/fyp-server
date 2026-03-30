# we downloaded supabase locally 
#npx supabase start will spin up docker conatiner with postgresql,auth apiis and etc 
#pulls all images from dockerhub and start docker
#it will create the supabase backened service 


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.routes.userRoutes import router as userRoutes
from src.routes.projectRoutes import router as projectRoutes
from src.routes.projectFilesRoutes import router as projectFilesRoutes
from src.routes.chatRoutes import router as chatRoutes

import traceback
import logging


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="AI_study_companion",
    description="Backend for AI Study Companion",
    version="1.0.0",
)

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}", exc_info=True)
    traceback.print_exc()
    return {
        "detail": f"{type(exc).__name__}: {str(exc)}"
    }

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(userRoutes, prefix="/api/user")
app.include_router(projectRoutes, prefix="/api/projects")
app.include_router(projectFilesRoutes, prefix="/api/projects")
app.include_router(chatRoutes, prefix="/api/chats")

"""
@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "1.0.0"}
"""
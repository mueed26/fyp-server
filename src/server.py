import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.routes.userRoutes import router as userRoutes
from src.routes.projectRoutes import router as projectRoutes
from src.routes.projectFilesRoutes import router as projectFilesRoutes
from src.routes.chatRoutes import router as chatRoutes
from src.routes.featureRoutes import router as featureRoutes
from src.routes.notesRoutes import router as notes_router
from src.routes.paymentRoutes import router as payment_router  # NEW
from src.config.logging import configure_logging, get_logger
from src.middleware.logging_middleware import LoggingMiddleware

configure_logging()
logger = get_logger(__name__)
logger.info("initializing_application", version="1.0.0")

app = FastAPI(
    title="AI_study_companion",
    description="Backend for AI Study Companion",
    version="1.0.0",
)

app.add_middleware(LoggingMiddleware)


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception",
        exc_type=type(exc).__name__,
        error=str(exc),
        path=request.url.path,
        method=request.method,
        exc_info=True,
    )
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {str(exc)}"},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("middleware_configured")

app.include_router(userRoutes, prefix="/api/user")
app.include_router(projectRoutes, prefix="/api/projects")
app.include_router(projectFilesRoutes, prefix="/api/projects")
app.include_router(chatRoutes, prefix="/api/chats")
app.include_router(featureRoutes, prefix="/api/projects")
app.include_router(notes_router, prefix="/api/projects", tags=["notes"])
app.include_router(payment_router, prefix="/api/payments", tags=["payments"])  # NEW
logger.info("routes_registered", route_count=7)


@app.get("/health")
async def health_check():
    logger.debug("health_check_called")
    return {"status": "healthy", "version": "1.0.0"}


logger.info("application_ready")
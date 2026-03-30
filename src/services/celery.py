from celery import Celery
from src.config.index import appConfig
from src.rag.ingestion.index import process_document

celery_app = Celery(
    "multi-modal-rag",  # Name of the Celery App
    broker=appConfig["redis_url"],  # broker - Redis Queue - Tasks are queued
)


@celery_app.task
def perform_rag_ingestion_task(document_id: str):
    try:
        process_document_result = process_document(document_id)
        return (
            f"Document {process_document_result['document_id']} processed successfully"
        )
    except Exception as e:
        return f"Failed to process document {document_id}: {str(e)}"
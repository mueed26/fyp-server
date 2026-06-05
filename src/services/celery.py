from celery import Celery
from src.config.index import appConfig
from celery.signals import task_prerun, task_postrun, task_failure, worker_process_init
from src.config.logging import configure_logging, get_logger, set_request_id, clear_context

# Configure logging for Celery worker with dedicated log file
configure_logging(log_filename="worker.log")

from src.rag.ingestion.index import process_document
from src.features.generate import generate_all_features_for_document

celery_app = Celery(
    "multi-modal-rag",
    broker=appConfig["redis_url"],
)

# Disable Celery's logging hijacking to preserve structlog JSON output
celery_app.conf.update(
    worker_hijack_root_logger=False,
    worker_log_format='%(message)s',
    worker_task_log_format='%(message)s',
    worker_redirect_stdouts=False,
    worker_redirect_stdouts_level='WARNING',
)


@worker_process_init.connect
def init_worker_process(sender=None, **kwargs):
    logger = get_logger(__name__)
    logger.info("celery_worker_started", worker_name=sender)


@task_prerun.connect
def task_prerun_handler(task_id=None, task=None, args=None, kwargs=None, **extra):
    set_request_id(task_id)
    logger = get_logger(__name__)
    logger.info("task_started", task_id=task_id, task_name=task.name, args=args, kwargs=kwargs)


@task_postrun.connect
def task_postrun_handler(task_id=None, task=None, retval=None, state=None, **_kwargs):
    logger = get_logger(__name__)
    logger.info("task_completed", task_id=task_id, task_name=task.name, state=state, result=str(retval)[:200] if retval else None)
    clear_context()


@task_failure.connect
def task_failure_handler(task_id=None, exception=None, sender=None, **_kwargs):
    logger = get_logger(__name__)
    logger.error("task_failed", task_id=task_id, task_name=sender.name if sender else None, error=str(exception), exc_info=True)
    clear_context()


@celery_app.task
def perform_rag_ingestion_task(document_id: str):
    logger = get_logger(__name__)
    logger.info("processing_document", document_id=document_id)
    try:
        process_document_result = process_document(document_id)
        # Trigger feature generation after ingestion completes
        perform_feature_generation_task.delay(document_id)
        logger.info(
            "document_processed_successfully",
            document_id=process_document_result.get("document_id"),
            chunks_created=process_document_result.get("chunks_created"),
        )
        return f"Document {process_document_result['document_id']} processed successfully"
    except Exception as e:
        logger.error("document_processing_failed", document_id=document_id, error=str(e), exc_info=True)
        return f"Failed to process document {document_id}: {str(e)}"


@celery_app.task
def perform_feature_generation_task(document_id: str):
    # FIXED: was missing all logging
    logger = get_logger(__name__)
    logger.info("feature_generation_started", document_id=document_id)
    try:
        result = generate_all_features_for_document(document_id)
        logger.info("feature_generation_completed", document_id=document_id, result=str(result)[:200] if result else None)
        return f"Features for document {document_id} generated successfully"
    except Exception as e:
        logger.error("feature_generation_failed", document_id=document_id, error=str(e), exc_info=True)
        return f"Failed to generate features for {document_id}: {str(e)}"
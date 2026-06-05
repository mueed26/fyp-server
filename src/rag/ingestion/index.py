
import os
import time
import tempfile

from src.services.supabase import supabase
from src.services.llm import openAI
from src.services.awsS3 import s3_client
from src.config.index import appConfig
from src.config.logging import get_logger
from src.rag.ingestion.utils import (
    partition_document,
    analyze_elements,
    separate_content_types,
    get_page_number,
    create_ai_summary,
)
from src.models.index import ProcessingStatus
from unstructured.chunking.title import chunk_by_title
from src.services.webScrapper import scrapingbee_client

logger = get_logger(__name__)


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────

def process_document(document_id: str) -> dict:
    """
    Full ingestion pipeline for a single document:

    Step 1 – Download from S3 (file) or crawl the URL, then partition into elements.
    Step 2 – Chunk elements by title structure.
    Step 3 – AI-summarise chunks that contain tables or images.
    Step 4 – Vectorise chunk content and store in the database.

    `processing_details` on the project_documents record is updated at each
    stage so the frontend can show live progress.
    """
    logger.info("process_document_started", document_id=document_id)
    try:
        update_status_in_database(document_id, ProcessingStatus.PROCESSING)

        document_result = (
            supabase.table("project_documents")
            .select("*")
            .eq("id", document_id)
            .execute()
        )
        if not document_result.data:
            raise Exception(f"No project_document record found for id: {document_id}")

        document = document_result.data[0]
        logger.info(
            "document_record_retrieved",
            document_id=document_id,
            filename=document.get("filename"),
            source_type=document.get("source_type"),
        )

        # Step 1 – Partition
        update_status_in_database(document_id, ProcessingStatus.PARTITIONING)
        elements_summary, elements = download_content_and_partition(document_id, document)
        logger.info("partitioning_complete", document_id=document_id, summary=elements_summary)

        update_status_in_database(
            document_id,
            ProcessingStatus.CHUNKING,
            {ProcessingStatus.PARTITIONING.value: {"elements_found": elements_summary}},
        )

        # Step 2 – Chunk
        source_type = document.get("source_type", "file")
        chunks, chunking_metrics = chunk_elements_by_title(elements)
        logger.info("chunking_complete", document_id=document_id, metrics=chunking_metrics)

        update_status_in_database(
            document_id,
            ProcessingStatus.SUMMARISING,
            {ProcessingStatus.CHUNKING.value: chunking_metrics},
        )

        # Step 3 – Summarise
        processed_chunks = summarise_chunks(chunks, document_id, source_type=source_type)
        update_status_in_database(document_id, ProcessingStatus.VECTORIZATION)

        # Step 4 – Vectorise & store
        vectorize_chunks_summary_and_store_in_database(processed_chunks, document_id)
        update_status_in_database(document_id, ProcessingStatus.COMPLETED)

        logger.info("process_document_completed", document_id=document_id)
        return {"success": True, "document_id": document_id}

    except Exception as e:
        logger.error(
            "process_document_failed",
            document_id=document_id,
            error=str(e),
            exc_info=True,
        )
        raise Exception(f"Failed to process document {document_id}: {str(e)}")


# ─────────────────────────────────────────────
#  Database helpers
# ─────────────────────────────────────────────

def update_status_in_database(
    document_id: str, status: ProcessingStatus, details: dict = None
) -> None:
    """Merge `details` into processing_details and set the new status."""
    try:
        result = (
            supabase.table("project_documents")
            .select("processing_details")
            .eq("id", document_id)
            .execute()
        )
        if not result.data:
            raise Exception(f"No project_document record found for id: {document_id}")

        current_details = result.data[0]["processing_details"] or {}
        if details:
            current_details.update(details)

        update_result = (
            supabase.table("project_documents")
            .update(
                {
                    "processing_status": status.value,
                    "processing_details": current_details,
                }
            )
            .eq("id", document_id)
            .execute()
        )
        if not update_result.data:
            raise Exception(
                f"Update returned no data for document_id: {document_id}"
            )

    except Exception as e:
        raise Exception(f"Failed to update status in database: {str(e)}")


# ─────────────────────────────────────────────
#  Step 1 – Download & Partition
# ─────────────────────────────────────────────

def download_content_and_partition(document_id: str, document: dict) -> tuple:
    """
    Download the document from S3 (or crawl a URL), write it to a temp file,
    partition it into unstructured elements, then clean up the temp file.
    Returns (elements_summary, elements).
    """
    try:
        source_type = document["source_type"]
        temp_file_path = None

        if source_type == "file":
            s3_key = document["s3_key"]
            filename = document["filename"]
            file_type = filename.rsplit(".", 1)[-1].lower()
            temp_file_path = os.path.join(
                tempfile.gettempdir(), f"{document_id}.{file_type}"
            )
            logger.info(
                "downloading_from_s3",
                document_id=document_id,
                s3_key=s3_key,
                temp_file=temp_file_path,
            )
            s3_client.download_file(
                appConfig["s3_bucket_name"], s3_key, temp_file_path
            )
            elements = partition_document(temp_file_path, file_type)

        elif source_type == "url":
            url = document["source_url"]
            temp_file_path = os.path.join(
                tempfile.gettempdir(), f"{document_id}.html"
            )
            logger.info(
                "crawling_url", document_id=document_id, url=url, temp_file=temp_file_path
            )
            response = scrapingbee_client.get(url)
            with open(temp_file_path, "wb") as f:
                f.write(response.content)
            elements = partition_document(temp_file_path, "html", source_type="url")

        else:
            raise ValueError(f"Unknown source_type: {source_type}")

        elements_summary = analyze_elements(elements)
        os.remove(temp_file_path)
        logger.info(
            "download_and_partition_done",
            document_id=document_id,
            elements_summary=elements_summary,
        )
        return elements_summary, elements

    except Exception as e:
        raise Exception(
            f"Failed in Step 1 to download content and partition elements: {str(e)}"
        )


# ─────────────────────────────────────────────
#  Step 2 – Chunk
# ─────────────────────────────────────────────

def chunk_elements_by_title(elements: list) -> tuple:
    """
    Chunk elements by title structure, then ensure images end up in the
    chunk that matches their page number (chunk_by_title can misplace or
    drop image elements).
    Returns (chunks, chunking_metrics).
    """
    try:
        all_image_elements = [el for el in elements if type(el).__name__ == "Image"]
        logger.info(
            "chunking_started",
            total_elements=len(elements),
            image_elements=len(all_image_elements),
        )

        chunks = chunk_by_title(
            elements,
            max_characters=3000,
            new_after_n_chars=2400,
            combine_text_under_n_chars=500,
        )
        logger.info("chunk_by_title_done", chunk_count=len(chunks))

        chunks, placement_stats = _ensure_images_in_correct_chunks(
            chunks, all_image_elements
        )

        chunks_with_images = sum(
            1
            for c in chunks
            if any(
                type(e).__name__ == "Image"
                for e in (
                    getattr(getattr(c, "metadata", None), "orig_elements", None) or []
                )
            )
        )

        chunking_metrics = {
            "total_chunks": len(chunks),
            "chunks_with_images": chunks_with_images,
            "images_relocated": placement_stats["images_relocated"],
            "images_recovered": placement_stats["images_recovered"],
        }
        logger.info("chunking_metrics", **chunking_metrics)
        return chunks, chunking_metrics

    except Exception as e:
        logger.error("chunking_failed", error=str(e), exc_info=True)
        raise Exception(f"Failed to chunk elements by title: {str(e)}")


def _ensure_images_in_correct_chunks(chunks: list, all_image_elements: list) -> tuple:
    """
    Post-process chunks to fix image placement:
    - Remove images that landed in the wrong page's chunk (misplaced).
    - Detect images that chunk_by_title dropped entirely (missing).
    - Re-attach both sets to the chunk whose page number best matches the image.
    """
    stats = {"images_relocated": 0, "images_recovered": 0}
    if not all_image_elements or not chunks:
        return chunks, stats

    # Index original images by base64 hash for fast lookup
    original_b64s: dict = {}
    for img in all_image_elements:
        b64 = getattr(getattr(img, "metadata", None), "image_base64", None)
        if b64:
            original_b64s[b64] = img

    if not original_b64s:
        return chunks, stats

    correctly_placed_b64s: set = set()
    misplaced: list = []

    for chunk in chunks:
        orig = getattr(getattr(chunk, "metadata", None), "orig_elements", None)
        if not orig:
            continue
        chunk_page = getattr(chunk.metadata, "page_number", None)
        is_tuple = isinstance(orig, tuple)
        kept: list = []

        for el in orig:
            if type(el).__name__ != "Image":
                kept.append(el)
                continue
            b64 = getattr(getattr(el, "metadata", None), "image_base64", None)
            if not b64 or b64 not in original_b64s:
                kept.append(el)
                continue
            img_page = getattr(el.metadata, "page_number", None)
            if img_page is None or chunk_page is None or img_page == chunk_page:
                kept.append(el)
                correctly_placed_b64s.add(b64)
            else:
                misplaced.append(el)

        chunk.metadata.orig_elements = tuple(kept) if is_tuple else kept

    # Images that never appeared in any chunk at all
    missing: list = [
        img
        for b64, img in original_b64s.items()
        if b64 not in correctly_placed_b64s
        and not any(
            getattr(getattr(m, "metadata", None), "image_base64", None) == b64
            for m in misplaced
        )
    ]

    stats["images_relocated"] = len(misplaced)
    stats["images_recovered"] = len(missing)

    logger.info(
        "image_placement_check",
        correctly_placed=len(correctly_placed_b64s),
        misplaced=len(misplaced),
        missing=len(missing),
    )

    for img in misplaced + missing:
        img_page = getattr(getattr(img, "metadata", None), "page_number", None)
        target = _find_best_chunk_for_image(chunks, img_page)
        if target:
            _attach_element_to_chunk(target, img)
            logger.debug(
                "image_reattached",
                img_page=img_page,
                chunk_page=getattr(getattr(target, "metadata", None), "page_number", None),
            )

    return chunks, stats


def _find_best_chunk_for_image(chunks: list, image_page_number):
    """Return the chunk whose page number is closest to `image_page_number`."""
    if not chunks:
        return None
    if image_page_number is None:
        return chunks[0]

    exact = None
    closest = None
    closest_diff = float("inf")

    for chunk in chunks:
        chunk_page = getattr(getattr(chunk, "metadata", None), "page_number", None)
        if chunk_page is None:
            continue
        if chunk_page == image_page_number:
            exact = chunk
            break
        diff = abs(chunk_page - image_page_number)
        if diff < closest_diff:
            closest_diff = diff
            closest = chunk

    return exact or closest or chunks[0]


def _attach_element_to_chunk(chunk, element) -> None:
    """Append `element` to chunk.metadata.orig_elements (handles tuple or list)."""
    if not hasattr(chunk, "metadata") or chunk.metadata is None:
        return
    current = getattr(chunk.metadata, "orig_elements", None)
    if current is None:
        current = []
    elif isinstance(current, tuple):
        current = list(current)
    current.append(element)
    chunk.metadata.orig_elements = current


# ─────────────────────────────────────────────
#  Step 3 – Summarise
# ─────────────────────────────────────────────

def summarise_chunks(chunks: list, document_id: str, source_type: str = "file") -> list:
    """
    Iterate over chunks.  For each chunk that contains tables or images,
    call the LLM to produce a richer searchable summary.  Plain text chunks
    pass through unchanged.
    """
    try:
        processed_chunks = []
        total_chunks = len(chunks)

        for i, chunk in enumerate(chunks):
            current_chunk = i + 1
            update_status_in_database(
                document_id,
                ProcessingStatus.SUMMARISING,
                {
                    ProcessingStatus.SUMMARISING.value: {
                        "current_chunk": current_chunk,
                        "total_chunks": total_chunks,
                    }
                },
            )

            content_data = separate_content_types(chunk, source_type)

            if content_data["images"] or content_data["tables"]:
                logger.info(
                    "chunk_has_rich_content",
                    chunk=f"{current_chunk}/{total_chunks}",
                    images=len(content_data["images"]),
                    tables=len(content_data["tables"]),
                )
                enhanced_content = create_ai_summary(
                    content_data["text"],
                    content_data["tables"],
                    content_data["images"],
                )
            else:
                enhanced_content = content_data["text"]

            original_content: dict = {"text": content_data["text"]}
            if content_data["tables"]:
                original_content["tables"] = content_data["tables"]
            if content_data["images"]:
                original_content["images"] = content_data["images"]

            processed_chunks.append(
                {
                    "content": enhanced_content,
                    "original_content": original_content,
                    "type": content_data["types"],
                    "page_number": get_page_number(chunk, i),
                    "char_count": len(enhanced_content),
                }
            )

        logger.info(
            "summarisation_complete",
            document_id=document_id,
            total_chunks=total_chunks,
        )
        return processed_chunks

    except Exception as e:
        raise Exception(f"Failed to summarise chunks: {str(e)}")


# ─────────────────────────────────────────────
#  Step 4 – Vectorise & store
# ─────────────────────────────────────────────

def vectorize_chunks_summary_and_store_in_database(
    processed_chunks: list, document_id: str
) -> list:
    """
    Embed each chunk's content string (AI summary or raw text) in batches of 10,
    then insert each chunk + its embedding vector into document_chunks.
    Returns the list of stored chunk IDs.
    """
    try:
        ai_summary_list = [chunk["content"] for chunk in processed_chunks]
        batch_size = 10
        all_embeddings: list = []

        for start in range(0, len(ai_summary_list), batch_size):
            batch = ai_summary_list[start: start + batch_size]
            attempt = 0
            while True:
                try:
                    embeddings = openAI["embeddings"].embed_documents(batch)
                    all_embeddings.extend(embeddings)
                    logger.debug(
                        "batch_embedded",
                        batch_start=start,
                        batch_size=len(batch),
                    )
                    break
                except Exception as e:
                    attempt += 1
                    if attempt >= 3:
                        raise e
                    wait = 2 ** attempt
                    logger.warning(
                        "embedding_retry",
                        attempt=attempt,
                        wait_seconds=wait,
                        error=str(e),
                    )
                    time.sleep(wait)

        stored_chunk_ids: list = []
        for i, (chunk, embedding_vector) in enumerate(
            zip(processed_chunks, all_embeddings)
        ):
            result = (
                supabase.table("document_chunks")
                .insert(
                    {
                        **chunk,
                        "document_id": document_id,
                        "chunk_index": i,
                        "embedding": embedding_vector,
                    }
                )
                .execute()
            )
            stored_chunk_ids.append(result.data[0]["id"])

        logger.info(
            "vectorization_complete",
            document_id=document_id,
            chunks_stored=len(stored_chunk_ids),
        )
        return stored_chunk_ids

    except Exception as e:
        raise Exception(
            f"Failed to vectorize chunks and store in database: {str(e)}"
        )
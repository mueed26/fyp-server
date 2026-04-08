"""
Cross-referencing utility for past year paper matching.

Uses vector similarity search (RAG) to find which lecture topics
appeared in past year examination papers.
"""

from typing import List, Dict, Tuple
from src.services.supabase import supabase
from src.services.llm import openAI


def get_past_year_document_ids(project_id: str) -> List[str]:
    """Get document IDs tagged as past year papers in a project."""
    result = (
        supabase.table("project_documents")
        .select("id")
        .eq("project_id", project_id)
        .eq("source_tag", "past_year_paper")
        .eq("processing_status", "completed")
        .execute()
    )
    if not result.data:
        return []
    return [doc["id"] for doc in result.data]


def get_lecture_document_ids(project_id: str) -> List[str]:
    """Get document IDs tagged as lecture notes in a project."""
    result = (
        supabase.table("project_documents")
        .select("id")
        .eq("project_id", project_id)
        .eq("source_tag", "lecture_notes")
        .eq("processing_status", "completed")
        .execute()
    )
    if not result.data:
        return []
    return [doc["id"] for doc in result.data]


def cross_reference_topics(
    lecture_doc_ids: List[str],
    past_year_doc_ids: List[str],
    topics: List[str],
    similarity_threshold: float = 0.3,
    chunks_per_search: int = 5,
) -> Dict[str, bool]:
    """
    For each topic, perform vector similarity search against past year paper chunks.
    Returns a dict mapping topic -> is_exam_relevant (True/False).
    
    This uses the existing vector_search_document_chunks RPC function
    with OpenAI embeddings for semantic matching.
    """
    if not past_year_doc_ids or not topics:
        return {topic: False for topic in topics}

    embeddings = openAI["embeddings"]
    topic_relevance = {}

    for topic in topics:
        try:
            # Generate embedding for the topic
            topic_embedding = embeddings.embed_documents([topic])[0]

            # Search against past year paper chunks
            search_result = supabase.rpc(
                "vector_search_document_chunks",
                {
                    "query_embedding": topic_embedding,
                    "filter_document_ids": past_year_doc_ids,
                    "match_threshold": similarity_threshold,
                    "chunks_per_search": chunks_per_search,
                },
            ).execute()

            # If we found matching chunks, topic is exam-relevant
            topic_relevance[topic] = bool(search_result.data and len(search_result.data) > 0)

        except Exception as e:
            print(f"Error cross-referencing topic '{topic}': {e}")
            topic_relevance[topic] = False

    return topic_relevance


def get_past_year_content(past_year_doc_ids: List[str]) -> str:
    """Fetch original content from past year paper chunks."""
    if not past_year_doc_ids:
        return ""

    all_content = []
    for doc_id in past_year_doc_ids:
        result = (
            supabase.table("document_chunks")
            .select("original_content")
            .eq("document_id", doc_id)
            .order("chunk_index")
            .execute()
        )
        if result.data:
            for chunk in result.data:
                original = chunk.get("original_content", {})
                if isinstance(original, dict):
                    text = original.get("text", "")
                    if text:
                        all_content.append(text)
                elif isinstance(original, str):
                    all_content.append(original)

    return "\n\n".join(all_content)


def extract_key_topics(content: str) -> List[str]:
    """Extract key topics from content using LLM."""
    llm = openAI["features_llm"]

    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([
        ("user",
         "Extract the key topics and concepts from the following text. "
         "Return ONLY a numbered list of topics, one per line. "
         "Keep each topic to 3-8 words. Extract 10-20 topics.\n\n"
         "Text:\n{content}")
    ])

    # Use first 5000 chars to keep it fast
    truncated = content[:5000] if len(content) > 5000 else content
    response = llm.invoke(prompt.invoke({"content": truncated}))
    
    # Parse topics from response
    topics = []
    for line in str(response.content).strip().split("\n"):
        line = line.strip()
        # Remove numbering like "1.", "1)", "- "
        for prefix in [".", ")", "-", "*"]:
            if prefix in line[:4]:
                line = line.split(prefix, 1)[-1].strip()
                break
        if line and len(line) > 2:
            topics.append(line)

    return topics[:20]  # Cap at 20 topics
"""
Shared utilities for feature generation pipelines.

- get_document_chunks_content: Fetch chunk text content from DB for a document
- generate_title: Generate a title for content using LLM
- merge_contents: Merge multiple feature outputs into one via LLM

Supported feature types: summary, flashcards, practice_questions, mind_map.
(flashcards / practice_questions are JSON and are handled directly by the route,
not merged through merge_contents; mind_map is built from summary. So in practice
merge_contents only ever runs for `summary`.)
"""

from typing import List, Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from src.services.supabase import supabase
from src.services.llm import openAI
from src.config.logging import get_logger

logger = get_logger(__name__)


def get_document_chunks_content(document_id: str) -> List[str]:
    result = (
        supabase.table("document_chunks")
        .select("original_content")
        .eq("document_id", document_id)
        .order("chunk_index")
        .execute()
    )

    if not result.data:
        return []

    contents = []
    for chunk in result.data:
        original = chunk.get("original_content", {})
        if isinstance(original, dict):
            text = original.get("text", "")
            if text:
                contents.append(text)
        elif isinstance(original, str):
            contents.append(original)

    return contents


def generate_title(content: str) -> str:
    """Generate a short descriptive title for the given content using LLM."""
    llm = openAI["features_llm"]

    prompt = ChatPromptTemplate.from_messages([
        ("user",
         "Generate a short, descriptive title (5-10 words) for the following content. "
         "Return ONLY the title, nothing else.\n\n{content}")
    ])

    truncated = content[:2000] if len(content) > 2000 else content
    response = llm.invoke(prompt.invoke({"content": truncated}))
    return str(response.content).strip().strip('"').strip("'")


def merge_contents(contents: List[Dict[str, Optional[str]]], source_type: str) -> str:
    """
    Merge multiple feature outputs (from multiple documents) into one cohesive output.

    Args:
        contents: List of dicts with 'title' and 'content' keys
        source_type: currently only 'summary' is merged through this path
                     (mind_map merges its underlying summaries with source_type='summary')

    Returns:
        Merged Markdown content string
    """
    llm = openAI["features_llm"]

    formatted = []
    for item in contents:
        title = item.get("title") or ""
        content = item.get("content") or ""
        formatted.append(f"title:{title},content:{content}")

    contents_str = "--==|==--".join(formatted)
    count = len(contents)

    type_labels = {
        "summary": "summaries",
    }
    type_label = type_labels.get(source_type, source_type)

    logger.info("merging_contents", source_type=source_type, count=count)

    prompt = ChatPromptTemplate.from_messages([
        ("user",
         f"You are a professional content synthesizer. Merge the following "
         f"{count} {type_label} into a single, polished, cohesive document.\n"
         f"Each item is separated by the marker: \"--==|==--\".\n\n"
         f"Input:\n{{context}}\n\n"
         f"Output requirements:\n"
         f"1. Structure:\n"
         f"   - Clear, logically organized Markdown document.\n"
         f"   - Use headings (##) for major sections.\n"
         f"   - Use bullet points (-) for key concepts.\n"
         f"2. Style & Clarity:\n"
         f"   - Preserve all essential ideas from the originals.\n"
         f"   - Avoid repetition, filler, or irrelevant content.\n"
         f"   - Factual, neutral, professional tone.\n"
         f"   - Highlight important terms using **bold**.\n"
         f"3. Output:\n"
         f"   - Only return Markdown content; no explanations outside the Markdown.\n")
    ])

    response = llm.invoke(prompt.invoke({"context": contents_str}))
    return str(response.content)
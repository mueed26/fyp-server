"""
Shared utilities for feature generation pipelines.

- get_document_chunks_content: Fetch chunk text content from DB for a document
- generate_title: Generate a title for content using LLM
- merge_contents: Merge multiple feature outputs into one via LLM
"""

from typing import List, Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from src.services.supabase import supabase
from src.services.llm import openAI


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
    """
    Generate a short descriptive title for the given content using LLM.
    """
    llm = openAI["features_llm"]
    
    prompt = ChatPromptTemplate.from_messages([
        ("user", 
         "Generate a short, descriptive title (5-10 words) for the following content. "
         "Return ONLY the title, nothing else.\n\n{content}")
    ])
    
    truncated = content[:2000] if len(content) > 2000 else content
    messages = prompt.invoke({"content": truncated})
    response = llm.invoke(messages)
    
    return str(response.content).strip().strip('"').strip("'")


def merge_contents(contents: List[Dict[str, Optional[str]]], source_type: str) -> str:
    """
    Merge multiple feature outputs (from multiple documents) into a single cohesive output.
    
    Args:
        contents: List of dicts with 'title' and 'content' keys
        source_type: One of 'summary', 'faq', 'study_guide', 'briefing_doc'
    
    Returns:
        Merged content string
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
        "faq": "FAQs",
        "study_guide": "study guides",
        "briefing_doc": "briefing documents",
    }
    type_label = type_labels.get(source_type, source_type)
    
    prompt = ChatPromptTemplate.from_messages([
        ("user",
         f"You are a professional content synthesizer. Your task is to merge the following "
         f"{count} {type_label} into a single, polished, and cohesive document.\n"
         f"Each item is separated by the marker: \"--==|==--\".\n\n"
         f"Input:\n{{context}}\n\n"
         f"Output requirements:\n"
         f"1. Structure:\n"
         f"   - Produce a clear, logically organized Markdown document.\n"
         f"   - Use headings (##) for major sections if applicable.\n"
         f"   - Use bullet points (-) for key concepts or takeaways.\n"
         f"2. Style & Clarity:\n"
         f"   - Preserve all essential ideas from the originals.\n"
         f"   - Avoid repetition, filler, or irrelevant content.\n"
         f"   - Keep the tone factual, neutral, and professional.\n"
         f"   - Highlight important terms using **bold**.\n"
         f"3. Output:\n"
         f"   - Only return Markdown content; do not include explanations outside of Markdown.\n")
    ])
    
    messages = prompt.invoke({"context": contents_str})
    response = llm.invoke(messages)
    
    return str(response.content)
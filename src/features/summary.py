"""
Summary Generation Pipeline - Exam Aware.

Consumes the exam linkage structure from cross_reference.py. Each linkage pairs
a real lecture excerpt with the real past-year exam question that matched it, so
the summary can point to exactly what was tested and how.
"""

from typing import List, Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from src.services.llm import openAI
from src.features.cross_reference import format_linkages_for_prompt
from src.config.logging import get_logger

logger = get_logger(__name__)

TOKEN_MAX = 4000


def _approximate_tokens(text: str) -> int:
    return len(text) // 4


def _length_function(documents: List[Document]) -> int:
    return sum(_approximate_tokens(doc.page_content) for doc in documents)


def _split_list_of_docs(docs: List[Document], token_max: int) -> List[List[Document]]:
    batches, current_batch, current_length = [], [], 0
    for doc in docs:
        doc_length = _approximate_tokens(doc.page_content)
        if current_length + doc_length > token_max and current_batch:
            batches.append(current_batch)
            current_batch, current_length = [], 0
        current_batch.append(doc)
        current_length += doc_length
    if current_batch:
        batches.append(current_batch)
    return batches


def _reduce(docs: List[Document]) -> str:
    llm = openAI["features_llm"]
    prompt = ChatPromptTemplate.from_messages([
        ("user",
         "The following is a set of summaries:\n{docs}\n\n"
         "Distill these into a **final, consolidated summary** in **Markdown format**.\n"
         "Use headings (##) for main sections, bullet points (-) for key points, "
         "and **bold** for important terms. Keep it clear and concise.")
    ])
    docs_text = "\n\n".join(doc.page_content for doc in docs)
    response = llm.invoke(prompt.invoke({"docs": docs_text}))
    return str(response.content)


def generate_summary(
    contents: List[str],
    exam_linkages: Optional[Dict] = None,
) -> str:
    """
    Generate a summary from document content, optionally exam-focused.

    Args:
        contents: List of text chunks from lecture notes
        exam_linkages: linkage structure from cross_reference.cross_reference_chunks(),
            or None. Contains real lecture<->exam excerpt pairs with similarity scores.
    """
    if not contents:
        return ""

    has_exam = bool(exam_linkages and exam_linkages.get("linkages"))
    logger.info("generating_summary", chunk_count=len(contents), has_exam_linkages=has_exam)

    all_text = "\n\n".join(contents)
    llm = openAI["features_llm"]

    # -- Exam-aware summary --------------------------------------------------
    if has_exam:
        linkage_block = format_linkages_for_prompt(exam_linkages)
        coverage_pct = int(exam_linkages["exam_coverage_score"] * 100)
        logger.info("exam_aware_summary_started", coverage_pct=coverage_pct,
                    linkages=len(exam_linkages["linkages"]))

        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert tutor creating an **exam-focused study summary**.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "=== EXAM LINKAGES ===\n"
             "Below are lecture sections paired with the ACTUAL past-year exam "
             "questions they matched (via semantic similarity). Higher similarity "
             "= this lecture content was tested more directly.\n\n"
             "{linkage_block}\n\n"
             "**Instructions:**\n"
             "- Comprehensive, detailed summary in **Markdown format**\n"
             "- Begin with an **Exam Focus** section: list the highest-similarity "
             "linked topics first, and for each, note what the past exam actually asked\n"
             "- Mark heavily-tested topics with up to 3 stars based on similarity\n"
             "- Use ## headings per major topic; bullet points (-) for key points; "
             "**bold** for important terms\n"
             "- End with **Predicted Important Topics** drawn from the linkages\n"
             "- Be thorough - aim for at least 2000 words\n"
             "- Two parts:\n"
             "  **Part 1: Comprehensive Summary** - all lecture topics\n"
             "  **Part 2: Exam Focus** - the linked topics with extra detail, "
             "explaining HOW each was tested and HOW it might be tested again\n")
        ])

        response = llm.invoke(prompt.invoke({
            "lecture_content": all_text[:15000],
            "linkage_block": linkage_block,
        }))
        result = str(response.content)
        logger.info("exam_aware_summary_completed", result_length=len(result))
        return result

    # -- Standard summary (no past year paper) -------------------------------
    if len(contents) <= 80:
        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "Create a comprehensive, detailed summary of the following document content.\n\n"
             "**Instructions:**\n"
             "- Return in **Markdown format**\n"
             "- Use ## headings for main sections\n"
             "- Use bullet points (-) for key points\n"
             "- Emphasize important terms with **bold**\n"
             "- Include key definitions, formulas, examples where relevant\n"
             "- Be EXTREMELY thorough - this is the student's PRIMARY study resource\n"
             "- Aim for at least 1500-2000 words\n"
             "- Cover EVERY major topic with full explanations\n"
             "- Include definitions, examples, formulas, relationships between concepts\n\n"
             "Content:\n{content}")
        ])
        response = llm.invoke(prompt.invoke({"content": all_text}))
        result = str(response.content)
        logger.info("standard_summary_completed", result_length=len(result))
        return result

    # -- Large document: map / reduce ----------------------------------------
    logger.info("large_document_map_reduce_started", chunk_count=len(contents))
    map_prompt = ChatPromptTemplate.from_messages([
        ("user", "Write a detailed summary of the following:\n\n{context}")
    ])

    summaries = []
    for content in contents:
        response = llm.invoke(map_prompt.invoke({"context": content}))
        summaries.append(str(response.content))

    collapsed = [Document(page_content=s) for s in summaries]
    while _length_function(collapsed) > TOKEN_MAX:
        batches = _split_list_of_docs(collapsed, TOKEN_MAX)
        collapsed = [Document(page_content=_reduce(batch)) for batch in batches]

    result = _reduce(collapsed)
    logger.info("map_reduce_summary_completed", result_length=len(result))
    return result
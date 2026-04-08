"""
Summary Generation Pipeline - Exam Aware.

- Generates exam-focused summaries when past year papers are available
- Highlights topics that appeared in past exams
- Covers both lecture content and exam-relevant material
"""

from typing import List, Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from src.services.llm import openAI

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
    past_year_content: Optional[str] = None,
    exam_relevant_topics: Optional[Dict[str, bool]] = None,
) -> str:
    """
    Generate a summary from document content, optionally exam-focused.
    
    Args:
        contents: List of text chunks from lecture notes
        past_year_content: Combined text from past year papers (if available)
        exam_relevant_topics: Dict of topic -> is_exam_relevant from RAG cross-referencing
    """
    if not contents:
        return ""

    all_text = "\n\n".join(contents)
    llm = openAI["features_llm"]

    # Exam-aware summary
    if past_year_content and exam_relevant_topics:
        exam_topics = [t for t, relevant in exam_relevant_topics.items() if relevant]
        non_exam_topics = [t for t, relevant in exam_relevant_topics.items() if not relevant]

        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert tutor creating an **exam-focused study summary** for students.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "=== PAST YEAR EXAMINATION PAPER ===\n{past_year_content}\n\n"
             "=== TOPICS THAT APPEARED IN PAST EXAMS (identified via semantic matching) ===\n"
             "{exam_topics}\n\n"
             "=== TOPICS NOT YET TESTED IN EXAMS ===\n"
             "{non_exam_topics}\n\n"
             "**Instructions:**\n"
             "- Create a comprehensive, detailed summary in **Markdown format**\n"
             "- Start with an **Exam Focus** section listing the most frequently tested topics\n"
             "- For each major topic, use ## headings\n"
             "- Mark topics that appeared in past exams with ⭐ in the heading\n"
             "- Only reference past year questions that relate to the lecture notes content\n"
             "- Ignore past year questions about topics not covered in the notes\n"
             "- Include key definitions, formulas, and concepts\n"
             "- Use bullet points (-) for key points\n"
             "- Use **bold** for important terms\n"
             "- End with a **Predicted Important Topics** section for potential exam questions\n"
             "- Be EXTREMELY thorough and detailed — aim for at least 2000 words\n"
             "- Structure in TWO main parts:\n"
             "  **Part 1: Comprehensive Summary** — detailed coverage of ALL lecture topics\n"
             "  **Part 2: Exam Focus** — topics that appeared in past exams with extra detail and exam tips\n"
             "- Include definitions, examples, formulas, and relationships between concepts\n"
             "- For exam topics, explain WHY they're important and HOW they might be tested\n"
             
             )
        ])

        response = llm.invoke(prompt.invoke({
            "lecture_content": all_text[:15000],
            "past_year_content": past_year_content[:10000],
            "exam_topics": "\n".join(f"- {t}" for t in exam_topics) if exam_topics else "None identified",
            "non_exam_topics": "\n".join(f"- {t}" for t in non_exam_topics) if non_exam_topics else "None",
        }))

        return str(response.content)

    # Standard summary (no past year paper)
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
             "- Be EXTREMELY thorough and detailed — this is the student's PRIMARY study resource\n"
             "- Aim for at least 1500-2000 words\n"
             "- Cover EVERY major topic with full explanations, not just bullet points\n"
             "- Include definitions, examples, formulas, and relationships between concepts\n\n"

             
             "Content:\n{content}")
        ])
        response = llm.invoke(prompt.invoke({"content": all_text}))
        return str(response.content)

    # Large document: map/reduce
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

    return _reduce(collapsed)
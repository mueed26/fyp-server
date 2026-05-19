"""
Study Guide Generation Pipeline.
- 80 or fewer chunks: single LLM call
- More than 80 chunks: map/reduce
"""

from typing import List
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
         "The following are study guide chunks:\n{docs}\n\n"
         "Distill into a single cohesive study guide. Maintain ALL key concepts, "
         "definitions, examples, formulas, and important details. Do NOT summarize or shorten — "
         "keep all the detail. Format in Markdown with clear headings and bullet points.")
    ])
    docs_text = "\n\n".join(doc.page_content for doc in docs)
    response = llm.invoke(prompt.invoke({"docs": docs_text}))
    return str(response.content)


def _single_call_study_guide(all_text: str) -> str:
    """Generate study guide in one LLM call."""
    llm = openAI["features_llm"]
    prompt = ChatPromptTemplate.from_messages([
        ("user",
         "Create a VERY DETAILED and COMPREHENSIVE study guide from the following document content.\n\n"
         "**Instructions:**\n"
         "- Cover EVERY topic and subtopic in the document — do not skip anything\n"
         "- Include ALL key concepts with full definitions and explanations\n"
         "- Include ALL formulas, equations, and technical details\n"
         "- Add examples and illustrations for each concept where relevant\n"
         "- Include relationships and comparisons between concepts\n"
         "- Highlight important points to remember for exams\n"
         "- Use ## headings for major topics\n"
         "- Use ### subheadings for subtopics\n"
         "- Use bullet points (-) for key points under each topic\n"
         "- Use **bold** for important terms and definitions\n"
         "- Be thorough — this is the student's PRIMARY study resource\n"
         "- Aim for at least 1500-2000 words\n"
         "- Return in Markdown format\n\n"
         "Content:\n{content}")
    ])
    response = llm.invoke(prompt.invoke({"content": all_text}))
    return str(response.content)


def generate_study_guide(contents: List[str]) -> str:
    if not contents:
        return ""

    # Small document: single call
    if len(contents) <= 80:
        all_text = "\n\n".join(contents)
        return _single_call_study_guide(all_text)

    # Large document: map/reduce
    llm = openAI["features_llm"]
    map_prompt = ChatPromptTemplate.from_messages([
        ("user",
         "Create detailed structured study notes for the following text. Include:\n"
         "- ALL key concepts with full definitions\n"
         "- ALL formulas, equations, technical details\n"
         "- Examples or illustrations for each concept\n"
         "- Important points to remember\n"
         "- Relationships between concepts\n"
         "Do NOT summarize — keep all detail.\n"
         "Format as bullet points with headings:\n\n{context}")
    ])

    guide_chunks = []
    for content in contents:
        response = llm.invoke(map_prompt.invoke({"context": content}))
        guide_chunks.append(str(response.content))

    collapsed = [Document(page_content=g) for g in guide_chunks]

    while _length_function(collapsed) > TOKEN_MAX:
        batches = _split_list_of_docs(collapsed, TOKEN_MAX)
        collapsed = [Document(page_content=_reduce(batch)) for batch in batches]

    return _reduce(collapsed)
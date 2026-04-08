"""
Study Guide Generation Pipeline.

- 10 or fewer chunks: single LLM call (cheap, fast)
- More than 10 chunks: map/reduce (handles large docs)
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
         "Distill into a single cohesive study guide. Maintain key concepts, "
         "definitions, examples, and main points. Format in Markdown with "
         "clear headings and bullet points.")
    ])
    docs_text = "\n\n".join(doc.page_content for doc in docs)
    response = llm.invoke(prompt.invoke({"docs": docs_text}))
    return str(response.content)


def _single_call_study_guide(all_text: str) -> str:
    """Generate study guide in one LLM call — used for small documents."""
    llm = openAI["features_llm"]
    prompt = ChatPromptTemplate.from_messages([
        ("user",
         "Create a comprehensive study guide from the following document content.\n\n"
         "**Instructions:**\n"
         "- Include key concepts and definitions\n"
         "- Add examples and illustrations where relevant\n"
         "- Highlight important points to remember\n"
         "- Use ## headings for major topics\n"
         "- Use bullet points (-) for key points\n"
         "- Use **bold** for important terms\n"
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
         "Create structured study notes for the following text. Include:\n"
         "- Key concepts / definitions\n"
         "- Examples or illustrations\n"
         "- Important points\n"
         "Format as bullet points:\n\n{context}")
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
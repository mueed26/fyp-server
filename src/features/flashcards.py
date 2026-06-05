"""
Flashcard Generation Pipeline.

Consumes the exam linkage structure from cross_reference.py so cards can be
weighted toward content that actually appeared in past exams, and the LLM sees
the real exam question text (not just a topic label).

Output format:
{
  "flashcards": [
    {
      "id": 1,
      "front": "...",
      "back": "...",
      "topic": "...",
      "is_past_year": true,
      "exam_similarity": 0.87,
      "difficulty": "medium"
    }
  ],
  "total": 20,
  "exam_relevant_count": 8
}
"""

import json
from typing import Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from src.services.llm import openAI
from src.features.cross_reference import format_linkages_for_prompt
from src.config.logging import get_logger

logger = get_logger(__name__)


def _clean_json(content: str) -> str:
    content = str(content).strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


def generate_flashcards(
    lecture_content: str,
    exam_linkages: Optional[Dict] = None,
) -> str:
    """
    Generate flashcards from lecture content, with exam awareness.

    Args:
        lecture_content: Combined text from lecture note chunks
        exam_linkages: linkage structure from cross_reference.cross_reference_chunks(), or None
    """
    llm = openAI["features_llm"]
    has_exam = bool(exam_linkages and exam_linkages.get("linkages"))
    logger.info("generating_flashcards", lecture_chars=len(lecture_content), has_exam_linkages=has_exam)

    if has_exam:
        linkage_block = format_linkages_for_prompt(exam_linkages)
        logger.info("exam_aware_flashcards", linkages=len(exam_linkages["linkages"]))

        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert tutor creating exam-focused flashcards.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "=== EXAM LINKAGES ===\n"
             "Lecture sections paired with the ACTUAL past-year exam questions they "
             "matched (higher similarity = tested more directly):\n\n{linkage_block}\n\n"
             "Generate 20-30 flashcards.\n\n"
             "**Priority rules:**\n"
             "- Create MORE cards (3-4) for linkages with high similarity; fewer (1) for low\n"
             "- For linked content, base the card on what the real exam question asked\n"
             "- Set is_past_year: true and exam_similarity to the linkage similarity for these\n"
             "- Also cover important lecture topics with NO linkage "
             "(is_past_year: false, exam_similarity: 0.0)\n"
             "- Mix difficulty: easy, medium, hard\n\n"
             "Return ONLY valid JSON, no other text:\n"
             '{{\n'
             '  "flashcards": [\n'
             '    {{"id": 1, "front": "...", "back": "...", "topic": "...", '
             '"is_past_year": true, "exam_similarity": 0.85, "difficulty": "medium"}}\n'
             '  ]\n'
             '}}')
        ])
        response = llm.invoke(prompt.invoke({
            "lecture_content": lecture_content[:15000],
            "linkage_block": linkage_block,
        }))
    else:
        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert tutor creating study flashcards.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "Generate at least 20 flash cards covering all major topics and concepts.\n\n"
             "**Instructions:**\n"
             "- Cover key definitions, concepts, formulas, important details\n"
             "- Mix difficulty: easy, medium, hard\n"
             "- Set is_past_year: false and exam_similarity: 0.0 for all cards\n\n"
             "Return ONLY valid JSON, no other text:\n"
             '{{\n'
             '  "flashcards": [\n'
             '    {{"id": 1, "front": "...", "back": "...", "topic": "...", '
             '"is_past_year": false, "exam_similarity": 0.0, "difficulty": "medium"}}\n'
             '  ]\n'
             '}}')
        ])
        response = llm.invoke(prompt.invoke({"lecture_content": lecture_content[:20000]}))

    content = _clean_json(response.content)
    try:
        parsed = json.loads(content)
        flashcards = parsed.get("flashcards", [])
        exam_count = sum(1 for f in flashcards if f.get("is_past_year"))
        parsed["total"] = len(flashcards)
        parsed["exam_relevant_count"] = exam_count
        logger.info("flashcards_generated", total=len(flashcards), exam_relevant=exam_count)
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        logger.warning("flashcards_json_parse_failed", raw_length=len(content))
        return content
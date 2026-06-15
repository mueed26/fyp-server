"""
Flashcard Generation Pipeline.

Consumes the exam linkage structure from cross_reference.py so cards can be
weighted toward content that actually appeared in past exams, and the LLM sees
the real exam question text (not just a topic label).
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
    """Generate flashcards from lecture content, with exam awareness."""
    llm = openAI["features_llm"]
    has_exam = bool(exam_linkages and exam_linkages.get("linkages"))
    logger.info("generating_flashcards", lecture_chars=len(lecture_content), has_exam_linkages=has_exam)

    if has_exam:
        linkage_block = format_linkages_for_prompt(exam_linkages)
        linkage_count = len(exam_linkages["linkages"])
        logger.info("exam_aware_flashcards", linkages=linkage_count)

        # We want MANY exam-relevant cards — at least 2 per linkage
        min_exam_cards = max(linkage_count * 2, 8)

        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert tutor creating exam-focused flashcards.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "=== EXAM LINKAGES ===\n"
             "Lecture sections paired with the ACTUAL past-year exam questions they "
             "matched (higher similarity = tested more directly):\n\n{linkage_block}\n\n"
             "**MANDATORY OUTPUT REQUIREMENTS:**\n"
             "- Generate at LEAST {min_exam_cards} EXAM-RELEVANT cards (is_past_year: true)\n"
             "- For EACH linkage above, produce 2-3 flashcards covering different angles\n"
             "- Then add 8-15 ADDITIONAL cards covering important lecture topics with "
             "NO linkage (is_past_year: false)\n"
             "- Total target: 25-35 flashcards\n\n"
             "**Rules for exam-relevant cards (is_past_year: true):**\n"
             "- Base the card on what the real exam question asked\n"
             "- Set exam_similarity to the linkage similarity score\n"
             "- Different cards from the same linkage should test different aspects\n"
             "  (e.g., definition vs. example vs. application)\n\n"
             "**Rules for non-exam cards (is_past_year: false):**\n"
             "- Cover lecture topics that didn't appear in the linkages\n"
             "- Set exam_similarity: 0.0\n\n"
             "**General rules:**\n"
             "- Mix difficulty: easy, medium, hard\n"
             "- Front: clear question. Back: complete answer (2-3 sentences)\n\n"
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
            "min_exam_cards": min_exam_cards,
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
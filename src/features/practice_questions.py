"""
Practice Questions Generation Pipeline.

Generates structured JSON: MCQ, short answer, and paragraph/essay questions.

Consumes the exam linkage structure from cross_reference.py. In exam-aware mode
we make two LLM calls to guarantee minimum counts:
  Call 1 - recreate the real past-year questions found in the linkages
  Call 2 - generate fresh lecture-only questions on untested material
then merge both sets.
"""

import json
from typing import List, Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from src.services.llm import openAI
from src.features.cross_reference import format_linkages_for_prompt, get_matched_exam_text
from src.config.logging import get_logger

logger = get_logger(__name__)


def _parse_json_response(content: str) -> Optional[dict]:
    content = str(content).strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _generate_past_year_questions(llm, lecture_content: str, linkage_block: str, matched_exam_text: str, linkage_count: int) -> dict:
    """Call 1: recreate the real past-year questions surfaced in the linkages."""
    prompt = ChatPromptTemplate.from_messages([
        ("user",
         "You are an expert examiner. Your job is to RECREATE every past-year exam "
         "question that is relevant to the lecture notes below.\n\n"
         "=== LECTURE NOTES ===\n{lecture_content}\n\n"
         "=== EXAM LINKAGES (lecture content paired with the real exam questions it matched) ===\n"
         "{linkage_block}\n\n"
         "=== RAW MATCHED EXAM EXCERPTS ===\n{matched_exam_text}\n\n"
         "**MANDATORY OUTPUT REQUIREMENTS:**\n"
         "- You MUST produce AT LEAST {min_questions} questions across all categories combined\n"
         "- EVERY linkage above should produce at least 1 question — aim for 1-2 per linkage\n"
         "- Distinct linkages may share an exam excerpt — that's fine, derive multiple "
         "questions from the same excerpt if it covers multiple sub-topics\n"
         "- Set is_past_year: TRUE for EVERY question in this batch (no exceptions)\n\n"
         "**RULES:**\n"
         "- Recreate each distinct exam question you can identify from the linkages\n"
         "- Do NOT invent questions about topics absent from the lecture notes\n"
         "- Include diagram/drawing questions if present\n"
         "- Categorise each as mcq, short_answer, or paragraph (use the natural exam format)\n"
         "- MCQs: 4 options (A-D) + correct answer + explanation\n"
         "- short_answer: model answer (2-3 sentences)\n"
         "- paragraph: marks (5-15) + detailed model answer\n\n"
         "Return ONLY valid JSON:\n"
         '{{\n'
         '  "mcq": [{{"id": 1, "question": "...", "options": ["A) ...","B) ...","C) ...","D) ..."], '
         '"correct_answer": "A", "explanation": "...", "topic": "...", "is_past_year": true, "difficulty": "medium"}}],\n'
         '  "short_answer": [{{"id": 1, "question": "...", "model_answer": "...", "topic": "...", '
         '"is_past_year": true, "difficulty": "easy"}}],\n'
         '  "paragraph": [{{"id": 1, "question": "...", "model_answer": "...", "marks": 10, "topic": "...", '
         '"is_past_year": true, "difficulty": "hard"}}]\n'
         '}}')
    ])

    # Target: at least as many questions as we have linkages (1-per-linkage minimum)
    min_questions = max(linkage_count, 5)

    response = llm.invoke(prompt.invoke({
        "lecture_content": lecture_content[:40000],
        "linkage_block": linkage_block,
        "matched_exam_text": matched_exam_text[:15000],
        "min_questions": min_questions,
    }))
    parsed = _parse_json_response(response.content)
    if not parsed:
        logger.warning("past_year_questions_json_parse_failed")
        return {"mcq": [], "short_answer": [], "paragraph": []}

    # Force is_past_year: true on every question this call returns (safety net)
    for category in ["mcq", "short_answer", "paragraph"]:
        for q in parsed.get(category, []):
            q["is_past_year"] = True

    return parsed


def _generate_lecture_only_questions(llm, lecture_content: str, linkage_block: str) -> dict:
    """Call 2: NEW questions from lecture material, prioritising untested topics."""
    prompt = ChatPromptTemplate.from_messages([
        ("user",
         "You are an expert examiner creating NEW practice questions from lecture notes.\n\n"
         "=== LECTURE NOTES ===\n{lecture_content}\n\n"
         "=== ALREADY COVERED BY PAST-YEAR QUESTIONS (do NOT duplicate these) ===\n"
         "{linkage_block}\n\n"
         "**MANDATORY MINIMUM COUNTS:**\n"
         "- MCQ: minimum 8 (aim 10-12)\n"
         "- Short Answer: minimum 4 (aim 6-8)\n"
         "- Paragraph: minimum 3 (aim 4-5)\n\n"
         "**RULES:**\n"
         "- Set is_past_year: FALSE for ALL questions in this batch\n"
         "- Prioritise topics NOT in the linkages above, but you may re-angle linked topics\n"
         "- Cover ALL major lecture topics; mix difficulty easy/medium/hard\n"
         "- MCQs: 4 options (A-D) + explanation; short_answer: 2-3 sentence model answer; "
         "paragraph: marks (5-15) + detailed model answer\n\n"
         "DO NOT return fewer than the minimum counts.\n\n"
         "Return ONLY valid JSON:\n"
         '{{\n'
         '  "mcq": [{{"id": 1, "question": "...", "options": ["A) ...","B) ...","C) ...","D) ..."], '
         '"correct_answer": "A", "explanation": "...", "topic": "...", "is_past_year": false, "difficulty": "medium"}}],\n'
         '  "short_answer": [{{"id": 1, "question": "...", "model_answer": "...", "topic": "...", '
         '"is_past_year": false, "difficulty": "easy"}}],\n'
         '  "paragraph": [{{"id": 1, "question": "...", "model_answer": "...", "marks": 10, "topic": "...", '
         '"is_past_year": false, "difficulty": "hard"}}]\n'
         '}}')
    ])
    response = llm.invoke(prompt.invoke({
        "lecture_content": lecture_content[:40000],
        "linkage_block": linkage_block,
    }))
    parsed = _parse_json_response(response.content)
    if not parsed:
        logger.warning("lecture_only_questions_json_parse_failed")
        return {"mcq": [], "short_answer": [], "paragraph": []}

    # Force is_past_year: false on every question this call returns
    for category in ["mcq", "short_answer", "paragraph"]:
        for q in parsed.get(category, []):
            q["is_past_year"] = False

    return parsed


def _merge_results(past_year: dict, lecture_only: dict) -> dict:
    merged = {"mcq": [], "short_answer": [], "paragraph": []}
    for category in ["mcq", "short_answer", "paragraph"]:
        combined = past_year.get(category, []) + lecture_only.get(category, [])
        for i, q in enumerate(combined):
            q["id"] = i + 1
        merged[category] = combined
    return merged


def generate_practice_questions(
    lecture_content: str,
    exam_linkages: Optional[Dict] = None,
) -> str:
    """Generate practice questions from lecture content, with exam awareness."""
    llm = openAI["features_llm"]
    has_exam = bool(exam_linkages and exam_linkages.get("linkages"))
    logger.info("generating_practice_questions", lecture_chars=len(lecture_content), has_exam_linkages=has_exam)

    if has_exam:
        linkage_block = format_linkages_for_prompt(exam_linkages)
        matched_exam_text = get_matched_exam_text(exam_linkages)
        linkage_count = len(exam_linkages["linkages"])
        logger.info("exam_aware_practice_questions", linkages=linkage_count)

        logger.info("generating_past_year_questions_call")
        past_year_qs = _generate_past_year_questions(llm, lecture_content, linkage_block, matched_exam_text, linkage_count)
        py_count = sum(len(past_year_qs.get(k, [])) for k in ["mcq", "short_answer", "paragraph"])
        logger.info("past_year_questions_generated", count=py_count, target_min=max(linkage_count, 5))

        logger.info("generating_lecture_only_questions_call")
        lecture_qs = _generate_lecture_only_questions(llm, lecture_content, linkage_block)
        lec_count = sum(len(lecture_qs.get(k, [])) for k in ["mcq", "short_answer", "paragraph"])
        logger.info("lecture_only_questions_generated", count=lec_count)

        parsed = _merge_results(past_year_qs, lecture_qs)
    else:
        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert examiner creating a comprehensive practice paper.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "**MANDATORY MINIMUM COUNTS:**\n"
             "- MCQ: minimum 15 (aim 18-20)\n"
             "- Short Answer: minimum 8 (aim 10-12)\n"
             "- Paragraph: minimum 5 (aim 6-8)\n\n"
             "**Instructions:**\n"
             "- Cover ALL major lecture topics; mix difficulty easy/medium/hard\n"
             "- MCQs: 4 options (A-D) + correct answer + explanation\n"
             "- short_answer: 2-3 sentence model answer; paragraph: marks (5-15) + detailed model answer\n"
             "- Set is_past_year: false for ALL questions\n\n"
             "DO NOT return fewer than the minimum counts.\n\n"
             "Return ONLY valid JSON:\n"
             '{{\n'
             '  "mcq": [{{"id": 1, "question": "...", "options": ["A) ...","B) ...","C) ...","D) ..."], '
             '"correct_answer": "A", "explanation": "...", "topic": "...", "is_past_year": false, "difficulty": "medium"}}],\n'
             '  "short_answer": [{{"id": 1, "question": "...", "model_answer": "...", "topic": "...", '
             '"is_past_year": false, "difficulty": "easy"}}],\n'
             '  "paragraph": [{{"id": 1, "question": "...", "model_answer": "...", "marks": 10, "topic": "...", '
             '"is_past_year": false, "difficulty": "hard"}}]\n'
             '}}')
        ])
        response = llm.invoke(prompt.invoke({"lecture_content": lecture_content[:50000]}))
        parsed = _parse_json_response(response.content)
        if not parsed:
            logger.warning("practice_questions_json_parse_failed")
            return str(response.content)

    mcq_count = len(parsed.get("mcq", []))
    short_count = len(parsed.get("short_answer", []))
    para_count = len(parsed.get("paragraph", []))
    all_questions = parsed.get("mcq", []) + parsed.get("short_answer", []) + parsed.get("paragraph", [])
    exam_count = sum(1 for q in all_questions if q.get("is_past_year"))

    parsed["total"] = mcq_count + short_count + para_count
    parsed["exam_relevant_count"] = exam_count
    parsed["breakdown"] = {"mcq": mcq_count, "short_answer": short_count, "paragraph": para_count}

    logger.info("practice_questions_generated", total=parsed["total"], exam_relevant=exam_count,
                mcq=mcq_count, short_answer=short_count, paragraph=para_count)
    return json.dumps(parsed, indent=2)
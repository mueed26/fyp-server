"""
Practice Questions Generation Pipeline.

Generates structured JSON containing:
- MCQ questions (4 options, 1 correct)
- Short answer questions
- Paragraph/essay questions (including diagram/design questions)

Cross-references with past year papers to mark exam-relevant questions.

FIX: Split exam-aware mode into two LLM calls to guarantee minimum counts.
The LLM was generating only 12-15 total when asked to do both in one call.
Now: Call 1 generates past-year-derived questions, Call 2 generates lecture-only
questions, then results are merged.
"""


import json
from typing import List, Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from src.services.llm import openAI


def _parse_json_response(content: str) -> Optional[dict]:
    """Parse LLM response into JSON, handling markdown fences."""
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


def _generate_past_year_questions(
    llm,
    lecture_content: str,
    past_year_content: str,
    exam_topics: List[str],
) -> dict:
    """
    Call 1: Generate questions derived from past year papers.
    Only includes questions whose topics appear in the lecture notes.
    """
    prompt = ChatPromptTemplate.from_messages([
        ("user",
         "You are an expert examiner. Your task is to extract and recreate ALL questions "
         "from the past year examination paper that are relevant to the lecture notes.\n\n"
         "=== LECTURE NOTES ===\n{lecture_content}\n\n"
         "=== PAST YEAR EXAMINATION PAPER ===\n{past_year_content}\n\n"
         "=== TOPICS THAT APPEARED IN PAST EXAMS ===\n{exam_topics}\n\n"
         "**RULES:**\n"
         "- Include EVERY question from the past year paper whose concept appears in the lecture notes\n"
         "- Do NOT skip any relevant past year question\n"
         "- Do NOT include questions about topics not covered in the lecture notes\n"
         "- Set is_past_year: true for ALL questions\n"
         "- Include diagram/drawing questions if present (e.g., 'Draw and explain...', 'Design a...')\n"
         "- Categorise each question as mcq, short_answer, or paragraph based on the original format\n"
         "- For MCQs: provide 4 options (A, B, C, D) and the correct answer\n"
         "- For short_answer: provide a model answer (2-3 sentences)\n"
         "- For paragraph: provide marks (5-15) and a detailed model answer\n\n"
         "Return ONLY valid JSON:\n"
         '{{\n'
         '  "mcq": [{{"id": 1, "question": "...", "options": ["A) ...", "B) ...", "C) ...", "D) ..."], '
         '"correct_answer": "A", "explanation": "...", "topic": "...", "is_past_year": true, "difficulty": "medium"}}],\n'
         '  "short_answer": [{{"id": 1, "question": "...", "model_answer": "...", "topic": "...", '
         '"is_past_year": true, "difficulty": "easy"}}],\n'
         '  "paragraph": [{{"id": 1, "question": "...", "model_answer": "...", "marks": 10, "topic": "...", '
         '"is_past_year": true, "difficulty": "hard"}}]\n'
         '}}')
    ])

    response = llm.invoke(prompt.invoke({
        "lecture_content": lecture_content[:40000],
        "past_year_content": past_year_content[:30000],
        "exam_topics": "\n".join(f"- {t}" for t in exam_topics) if exam_topics else "None identified",
    }))

    parsed = _parse_json_response(response.content)
    if not parsed:
        return {"mcq": [], "short_answer": [], "paragraph": []}
    return parsed


def _generate_lecture_only_questions(
    llm,
    lecture_content: str,
    non_exam_topics: List[str],
    exam_topics: List[str],
) -> dict:
    """
    Call 2: Generate additional practice questions purely from lecture notes.
    These are NEW questions the student hasn't seen in past papers.
    """
    prompt = ChatPromptTemplate.from_messages([
        ("user",
         "You are an expert examiner creating NEW practice questions from lecture notes.\n\n"
         "=== LECTURE NOTES ===\n{lecture_content}\n\n"
         "=== TOPICS ALREADY COVERED BY PAST YEAR QUESTIONS (do NOT duplicate these) ===\n"
         "{exam_topics}\n\n"
         "=== TOPICS NOT YET TESTED (prioritise these) ===\n"
         "{non_exam_topics}\n\n"
         "**MANDATORY MINIMUM COUNTS — you MUST generate AT LEAST this many:**\n"
         "- MCQ: minimum 8 questions (aim for 10-12)\n"
         "- Short Answer: minimum 4 questions (aim for 6-8)\n"
         "- Paragraph/Essay: minimum 3 questions (aim for 4-5)\n\n"
         "**RULES:**\n"
         "- Set is_past_year: false for ALL questions\n"
         "- Prioritise topics NOT yet tested in exams, but also cover exam topics from different angles\n"
         "- Cover ALL major topics from the lecture notes\n"
         "- Mix difficulty levels: easy, medium, hard\n"
         "- For MCQs: 4 options (A, B, C, D), include explanation\n"
         "- For short_answer: model answer in 2-3 sentences\n"
         "- For paragraph: include marks (5-15) and detailed model answer\n"
         "- Include diagram/design questions where relevant\n\n"
         "DO NOT return fewer than the minimum counts. This is non-negotiable.\n\n"
         "Return ONLY valid JSON:\n"
         '{{\n'
         '  "mcq": [{{"id": 1, "question": "...", "options": ["A) ...", "B) ...", "C) ...", "D) ..."], '
         '"correct_answer": "A", "explanation": "...", "topic": "...", "is_past_year": false, "difficulty": "medium"}}],\n'
         '  "short_answer": [{{"id": 1, "question": "...", "model_answer": "...", "topic": "...", '
         '"is_past_year": false, "difficulty": "easy"}}],\n'
         '  "paragraph": [{{"id": 1, "question": "...", "model_answer": "...", "marks": 10, "topic": "...", '
         '"is_past_year": false, "difficulty": "hard"}}]\n'
         '}}')
    ])

    response = llm.invoke(prompt.invoke({
        "lecture_content": lecture_content[:40000],
        "exam_topics": "\n".join(f"- {t}" for t in exam_topics) if exam_topics else "None",
        "non_exam_topics": "\n".join(f"- {t}" for t in non_exam_topics) if non_exam_topics else "None",
    }))

    parsed = _parse_json_response(response.content)
    if not parsed:
        return {"mcq": [], "short_answer": [], "paragraph": []}
    return parsed


def _merge_results(past_year: dict, lecture_only: dict) -> dict:
    """Merge two question sets, re-index IDs sequentially."""
    merged = {"mcq": [], "short_answer": [], "paragraph": []}

    for category in ["mcq", "short_answer", "paragraph"]:
        combined = past_year.get(category, []) + lecture_only.get(category, [])
        # Re-index IDs
        for i, q in enumerate(combined):
            q["id"] = i + 1
        merged[category] = combined

    return merged


def generate_practice_questions(
    lecture_content: str,
    past_year_content: Optional[str] = None,
    exam_relevant_topics: Optional[Dict[str, bool]] = None,
) -> str:
    """
    Generate practice questions from lecture content, with exam awareness.
    
    When past year papers are available:
      - Call 1: Extract/recreate all relevant past year questions (is_past_year: true)
      - Call 2: Generate 15+ NEW questions from lecture notes (is_past_year: false)
      - Merge both sets into final output
    
    When no past year papers:
      - Single call generating 25-35 questions from lecture notes
    """
    llm = openAI["features_llm"]

    if past_year_content and exam_relevant_topics:
        exam_topics = [t for t, relevant in exam_relevant_topics.items() if relevant]
        non_exam_topics = [t for t, relevant in exam_relevant_topics.items() if not relevant]

        # Call 1: Past year derived questions
        print("📝 Generating past year questions...")
        past_year_qs = _generate_past_year_questions(
            llm, lecture_content, past_year_content, exam_topics
        )
        py_count = sum(len(past_year_qs.get(k, [])) for k in ["mcq", "short_answer", "paragraph"])
        print(f"✅ Past year questions generated: {py_count}")

        # Call 2: Lecture-only questions
        print("📝 Generating lecture-only questions...")
        lecture_qs = _generate_lecture_only_questions(
            llm, lecture_content, non_exam_topics, exam_topics
        )
        lec_count = sum(len(lecture_qs.get(k, [])) for k in ["mcq", "short_answer", "paragraph"])
        print(f"✅ Lecture-only questions generated: {lec_count}")

        # Merge
        parsed = _merge_results(past_year_qs, lecture_qs)

    else:
        # No past year paper — single call, higher minimums
        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert examiner creating a comprehensive practice paper.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "**MANDATORY MINIMUM COUNTS — you MUST generate AT LEAST this many:**\n"
             "- MCQ: minimum 15 questions (aim for 18-20)\n"
             "- Short Answer: minimum 8 questions (aim for 10-12)\n"
             "- Paragraph/Essay: minimum 5 questions (aim for 6-8)\n\n"
             "**Instructions:**\n"
             "- Cover ALL major topics from the lecture notes\n"
             "- Mix difficulty: easy, medium, hard\n"
             "- For MCQs: 4 options (A, B, C, D), correct answer, explanation\n"
             "- For short_answer: model answer in 2-3 sentences\n"
             "- For paragraph: marks (5-15), detailed model answer\n"
             "- Include diagram/design questions where relevant\n"
             "- Set is_past_year: false for ALL questions\n\n"
             "DO NOT return fewer than the minimum counts. This is non-negotiable.\n\n"
             "Return ONLY valid JSON:\n"
             '{{\n'
             '  "mcq": [{{"id": 1, "question": "...", "options": ["A) ...", "B) ...", "C) ...", "D) ..."], '
             '"correct_answer": "A", "explanation": "...", "topic": "...", "is_past_year": false, "difficulty": "medium"}}],\n'
             '  "short_answer": [{{"id": 1, "question": "...", "model_answer": "...", "topic": "...", '
             '"is_past_year": false, "difficulty": "easy"}}],\n'
             '  "paragraph": [{{"id": 1, "question": "...", "model_answer": "...", "marks": 10, "topic": "...", '
             '"is_past_year": false, "difficulty": "hard"}}]\n'
             '}}')
        ])

        response = llm.invoke(prompt.invoke({
            "lecture_content": lecture_content[:50000],
        }))

        parsed = _parse_json_response(response.content)
        if not parsed:
            return str(response.content)

    # Add metadata
    mcq_count = len(parsed.get("mcq", []))
    short_count = len(parsed.get("short_answer", []))
    para_count = len(parsed.get("paragraph", []))
    all_questions = (
        parsed.get("mcq", []) +
        parsed.get("short_answer", []) +
        parsed.get("paragraph", [])
    )
    exam_count = sum(1 for q in all_questions if q.get("is_past_year"))
    parsed["total"] = mcq_count + short_count + para_count
    parsed["exam_relevant_count"] = exam_count
    parsed["breakdown"] = {
        "mcq": mcq_count,
        "short_answer": short_count,
        "paragraph": para_count,
    }

    print(f"📊 Final: {parsed['total']} questions ({exam_count} from past year)")
    return json.dumps(parsed, indent=2)
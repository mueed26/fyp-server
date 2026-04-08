"""
Flashcard Generation Pipeline.

Generates interactive flashcards as structured JSON.
Cross-references with past year papers to mark exam-relevant cards.

Output format:
{
  "flashcards": [
    {
      "id": 1,
      "front": "What is self-attention?",
      "back": "A mechanism that relates different positions of a single sequence...",
      "topic": "Attention Mechanisms",
      "is_past_year": true,
      "difficulty": "medium"
    }
  ],
  "total": 20,
  "exam_relevant_count": 8
}
"""

import json
from typing import List, Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from src.services.llm import openAI


def generate_flashcards(
    lecture_content: str,
    past_year_content: Optional[str] = None,
    exam_relevant_topics: Optional[Dict[str, bool]] = None,
) -> str:
    """
    Generate flashcards from lecture content, with exam awareness.
    
    Args:
        lecture_content: Combined text from lecture note chunks
        past_year_content: Combined text from past year paper chunks (if available)
        exam_relevant_topics: Dict of topic -> is_exam_relevant from RAG cross-referencing
    
    Returns:
        JSON string of flashcards
    """
    llm = openAI["features_llm"]

    # Build the prompt based on whether we have past year data
    if past_year_content and exam_relevant_topics:
        # Format exam-relevant topics
        exam_topics = [t for t, relevant in exam_relevant_topics.items() if relevant]
        non_exam_topics = [t for t, relevant in exam_relevant_topics.items() if not relevant]

        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert tutor creating exam-focused flashcards for students.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "=== PAST YEAR EXAMINATION PAPER ===\n{past_year_content}\n\n"
             "=== TOPICS THAT APPEARED IN PAST EXAMS (identified via semantic matching) ===\n"
             "{exam_topics}\n\n"
             "=== TOPICS NOT YET TESTED IN EXAMS ===\n"
             "{non_exam_topics}\n\n"
             "Generate 20-30 flashcards that cover BOTH the lecture notes AND past year exam content.\n\n"
             "**Priority rules:**\n"
             "- Create MORE flashcards for topics that appeared in past exams\n"
             "- Include past year exam questions ONLY if they relate to topics in the lecture notes\n"
             "- Do NOT create cards for past year topics not covered in the lecture notes\n"
             "- Also cover important lecture topics not yet tested\n"
             "- Set is_past_year to true for cards related to past exam topics\n"
             "- Set difficulty: easy, medium, or hard\n\n"
             "Return ONLY valid JSON in this exact format, no other text:\n"
             '{{\n'
             '  "flashcards": [\n'
             '    {{\n'
             '      "id": 1,\n'
             '      "front": "Question text here",\n'
             '      "back": "Answer text here",\n'
             '      "topic": "Topic name",\n'
             '      "is_past_year": true,\n'
             '      "difficulty": "medium"\n'
             '    }}\n'
             '  ]\n'
             '}}')
        ])

        response = llm.invoke(prompt.invoke({
            "lecture_content": lecture_content[:15000],
            "past_year_content": past_year_content[:10000],
            "exam_topics": "\n".join(f"- {t}" for t in exam_topics) if exam_topics else "None identified",
            "non_exam_topics": "\n".join(f"- {t}" for t in non_exam_topics) if non_exam_topics else "None",
        }))
    else:
        # No past year paper — generate from lecture notes only
        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert tutor creating study flashcards for students.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "Generate 20-30 flashcards covering all major topics and concepts.\n\n"
             "**Instructions:**\n"
             "- Cover key definitions, concepts, formulas, and important details\n"
             "- Mix difficulty levels: easy, medium, and hard\n"
             "- Set is_past_year to false for all cards (no past year paper provided)\n\n"
             "Return ONLY valid JSON in this exact format, no other text:\n"
             '{{\n'
             '  "flashcards": [\n'
             '    {{\n'
             '      "id": 1,\n'
             '      "front": "Question text here",\n'
             '      "back": "Answer text here",\n'
             '      "topic": "Topic name",\n'
             '      "is_past_year": false,\n'
             '      "difficulty": "medium"\n'
             '    }}\n'
             '  ]\n'
             '}}')
        ])

        response = llm.invoke(prompt.invoke({
            "lecture_content": lecture_content[:20000],
        }))

    # Parse and validate JSON
    content = str(response.content).strip()

    # Clean markdown fences
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    try:
        parsed = json.loads(content)
        # Add metadata
        flashcards = parsed.get("flashcards", [])
        exam_count = sum(1 for f in flashcards if f.get("is_past_year"))
        parsed["total"] = len(flashcards)
        parsed["exam_relevant_count"] = exam_count
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        # If JSON parsing fails, return as-is
        return content
"""
Practice Questions Generation Pipeline.

Generates structured JSON containing:
- MCQ questions (4 options, 1 correct)
- Short answer questions
- Paragraph/essay questions (including diagram/design questions)

Cross-references with past year papers to mark exam-relevant questions.
"""

import json
from typing import List, Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from src.services.llm import openAI


def generate_practice_questions(
    lecture_content: str,
    past_year_content: Optional[str] = None,
    exam_relevant_topics: Optional[Dict[str, bool]] = None,
) -> str:
    """
    Generate practice questions from lecture content, with exam awareness.
    """
    llm = openAI["features_llm"]

    if past_year_content and exam_relevant_topics:
        exam_topics = [t for t, relevant in exam_relevant_topics.items() if relevant]
        non_exam_topics = [t for t, relevant in exam_relevant_topics.items() if not relevant]

        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert examiner creating a comprehensive practice paper for students.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "=== PAST YEAR EXAMINATION PAPER ===\n{past_year_content}\n\n"
             "=== TOPICS THAT APPEARED IN PAST EXAMS (identified via semantic matching) ===\n"
             "{exam_topics}\n\n"
             "=== TOPICS NOT YET TESTED IN EXAMS ===\n"
             "{non_exam_topics}\n\n"
             "**CRITICAL RULES:**\n"
             "- Read the ENTIRE past year paper carefully — include EVERY question and sub-question from it\n"
             "- Each sub-part (a, b, c) of a past year question counts as a SEPARATE question\n"
             "- Mark ALL past year questions with is_past_year: true — DO NOT MISS ANY\n"
             "- THEN generate AT LEAST 20 additional practice questions from the lecture notes\n"
             "- Total questions should be AT LEAST 30-40\n"
             "- Do NOT include past year questions about topics not covered in the lecture notes\n"
             "- Mark all generated questions with is_past_year: false\n\n"
             "**DIAGRAM/DRAWING QUESTIONS:**\n"
             "- If the past year has diagram questions (e.g., 'Draw a use case diagram'), include them\n"
             "- For diagram question answers, provide a DETAILED TEXT-BASED DESCRIPTION that includes:\n"
             "  * All components/elements to draw (actors, nodes, entities, etc.)\n"
             "  * All relationships/connections between components with arrows notation\n"
             "  * A step-by-step structure like:\n"
             "    Actors: Actor1, Actor2, Actor3\n"
             "    Use Cases: UseCase1, UseCase2, UseCase3\n"
             "    Relationships:\n"
             "    - Actor1 → UseCase1\n"
             "    - Actor1 → UseCase2\n"
             "    - Actor2 → UseCase3\n"
             "    [System Boundary: System Name]\n"
             "  * Enough detail that a student can draw the complete diagram from your description\n\n"
             "**1. MCQ Questions (15-20 questions):**\n"
             "- 4 options each (A, B, C, D)\n"
             "- Include MCQs derived from past year papers (mark is_past_year: true)\n"
             "- Generate additional MCQs from lecture notes (mark is_past_year: false)\n"
             "- Cover all major topics from the lecture notes\n\n"
             "**2. Short Answer Questions (8-12 questions):**\n"
             "- Require 2-3 sentence answers\n"
             "- Include past year short questions that relate to lecture notes (mark is_past_year: true)\n"
             "- Generate additional ones from lecture notes (mark is_past_year: false)\n"
             "- Include 'define', 'compare', 'list', 'differentiate' type questions\n\n"
             "**3. Paragraph/Essay Questions (5-8 questions):**\n"
             "- Require detailed explanations (paragraph length)\n"
             "- Include marks allocation (5-15 marks each)\n"
             "- Include diagram/design questions with detailed text-based diagram descriptions in the model answer\n"
             "- Include past year essay/paragraph questions that relate to lecture notes (mark is_past_year: true)\n"
             "- Generate additional ones from lecture notes (mark is_past_year: false)\n"
             "- Test deep understanding, analysis, and application\n\n"
             "**MARKING RULES:**\n"
             "- Every question taken from the past year paper MUST have is_past_year: true\n"
             "- Every question YOU generate from lecture notes MUST have is_past_year: false\n"
             "- Students need to clearly see which questions appeared in real exams\n\n"
             "Return ONLY valid JSON in this exact format, no other text:\n"
             '{{\n'
             '  "mcq": [\n'
             '    {{\n'
             '      "id": 1,\n'
             '      "question": "Question text",\n'
             '      "options": ["A) Option 1", "B) Option 2", "C) Option 3", "D) Option 4"],\n'
             '      "correct_answer": "A",\n'
             '      "explanation": "Why A is correct",\n'
             '      "topic": "Topic name",\n'
             '      "is_past_year": true,\n'
             '      "difficulty": "medium"\n'
             '    }}\n'
             '  ],\n'
             '  "short_answer": [\n'
             '    {{\n'
             '      "id": 1,\n'
             '      "question": "Question text",\n'
             '      "model_answer": "The expected answer in 2-3 sentences...",\n'
             '      "topic": "Topic name",\n'
             '      "is_past_year": false,\n'
             '      "difficulty": "easy"\n'
             '    }}\n'
             '  ],\n'
             '  "paragraph": [\n'
             '    {{\n'
             '      "id": 1,\n'
             '      "question": "Draw and explain a use case diagram for...",\n'
             '      "model_answer": "Components:\\nActors: Actor1, Actor2\\nUse Cases: UC1, UC2, UC3\\nRelationships:\\n- Actor1 → UC1\\n- Actor1 → UC2\\n- Actor2 → UC3\\n[System Boundary: System Name]\\n\\nExplanation: The diagram shows...",\n'
             '      "marks": 10,\n'
             '      "topic": "Topic name",\n'
             '      "is_past_year": true,\n'
             '      "difficulty": "hard"\n'
             '    }}\n'
             '  ]\n'
             '}}')
        ])

        response = llm.invoke(prompt.invoke({
            "lecture_content": lecture_content[:30000],
            "past_year_content": past_year_content[:25000],
            "exam_topics": "\n".join(f"- {t}" for t in exam_topics) if exam_topics else "None identified",
            "non_exam_topics": "\n".join(f"- {t}" for t in non_exam_topics) if non_exam_topics else "None",
        }))
    else:
        prompt = ChatPromptTemplate.from_messages([
            ("user",
             "You are an expert examiner creating a comprehensive practice paper for students.\n\n"
             "=== LECTURE NOTES ===\n{lecture_content}\n\n"
             "Generate a comprehensive practice paper. Create as many questions as possible to "
             "help students thoroughly prepare. Total should be AT LEAST 30 questions.\n\n"
             "**1. MCQ Questions (15-20 questions):**\n"
             "- 4 options each (A, B, C, D), one correct answer\n"
             "- Mix of difficulty levels (easy, medium, hard)\n"
             "- Cover ALL major topics from the lecture notes\n"
             "- Include conceptual, application, and analytical questions\n\n"
             "**2. Short Answer Questions (8-12 questions):**\n"
             "- Require 2-3 sentence answers\n"
             "- Cover definitions, concepts, comparisons, differences\n"
             "- Include 'define', 'compare', 'list', 'differentiate' type questions\n\n"
             "**3. Paragraph/Essay Questions (5-8 questions):**\n"
             "- Require detailed explanations (paragraph length)\n"
             "- Include marks allocation (5-15 marks each)\n"
             "- Include diagram/design questions where relevant\n"
             "- For diagram questions, provide detailed text-based descriptions in the model answer:\n"
             "  * List all components/elements to draw\n"
             "  * Show all relationships with arrow notation (→)\n"
             "  * Provide enough detail to draw the complete diagram\n"
             "- Test deep understanding, analysis, and application\n\n"
             "Set is_past_year to false for all questions (no past year paper provided).\n\n"
             "Return ONLY valid JSON in this exact format, no other text:\n"
             '{{\n'
             '  "mcq": [\n'
             '    {{\n'
             '      "id": 1,\n'
             '      "question": "Question text",\n'
             '      "options": ["A) Option 1", "B) Option 2", "C) Option 3", "D) Option 4"],\n'
             '      "correct_answer": "A",\n'
             '      "explanation": "Why A is correct",\n'
             '      "topic": "Topic name",\n'
             '      "is_past_year": false,\n'
             '      "difficulty": "medium"\n'
             '    }}\n'
             '  ],\n'
             '  "short_answer": [\n'
             '    {{\n'
             '      "id": 1,\n'
             '      "question": "Question text",\n'
             '      "model_answer": "The expected answer in 2-3 sentences...",\n'
             '      "topic": "Topic name",\n'
             '      "is_past_year": false,\n'
             '      "difficulty": "easy"\n'
             '    }}\n'
             '  ],\n'
             '  "paragraph": [\n'
             '    {{\n'
             '      "id": 1,\n'
             '      "question": "Question text",\n'
             '      "model_answer": "Detailed expected answer...",\n'
             '      "marks": 10,\n'
             '      "topic": "Topic name",\n'
             '      "is_past_year": false,\n'
             '      "difficulty": "hard"\n'
             '    }}\n'
             '  ]\n'
             '}}')
        ])

        response = llm.invoke(prompt.invoke({
            "lecture_content": lecture_content[:30000],
        }))

    # Parse and validate JSON
    content = str(response.content).strip()

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
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        return content
"""
Mind Map Generation Pipeline.

Takes a study guide (already generated) and produces a MindElixir-compatible
JSON mind map structure.

Input: Study guide text string
Output: MindElixir JSON string
"""

import json
from langchain_core.prompts import PromptTemplate
from src.services.llm import openAI


MIND_MAP_PROMPT = PromptTemplate.from_template("""
You are an expert tutor creating a DETAILED mind map for exam preparation.

Your task: Convert the study guide into a comprehensive MindElixir mind map.

**RULES:**
1. Return ONLY valid JSON in MindElixir format — no other text.
2. Node names should be concise (1-7 words).
3. Structure: root → main topics → subtopics → details (up to 4 levels deep).
4. Cover EVERY major topic and subtopic from the study guide.
5. Each main topic should have at least 3-5 children.
6. Include key terms, definitions, examples, and relationships as leaf nodes.
7. Aim for at least 30-50 total nodes to be comprehensive.

**JSON structure:**
{{
  "nodeData": {{
    "id": "root",
    "topic": "<Main Subject>",
    "children": [
      {{
        "id": "<unique_id>",
        "topic": "<Major Topic>",
        "children": [
          {{
            "id": "<unique_id>",
            "topic": "<Subtopic>",
            "children": [
              {{
                "id": "<unique_id>",
                "topic": "<Detail or Example>",
                "children": []
              }}
            ]
          }}
        ]
      }}
    ]
  }}
}}

**ID format:** Use simple unique IDs like "t1", "t1c1", "t1c1c1", "t2", "t2c1", etc.

Study Guide:
\"\"\"
{study_guide_text}
\"\"\"

Output the Mind Map as JSON only.
""")


def generate_mind_map(study_guide: str) -> str:
    """
    Generate a MindElixir-compatible mind map JSON from a study guide.
    """
    if not study_guide:
        return ""

    llm = openAI["features_llm"]

    messages = MIND_MAP_PROMPT.invoke({"study_guide_text": study_guide})
    response = llm.invoke(messages)

    content = str(response.content).strip()

    # Clean up markdown code fences if present
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    # Validate it's valid JSON
    try:
        parsed = json.loads(content)
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        return content
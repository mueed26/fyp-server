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
You are an expert tutor. Your task is to create a **Mind Map** from the provided study guide
that enhances students' understanding and retention of complex concepts.

Follow these rules strictly:
1. Return ONLY valid JSON in MindElixir format.
2. Use short node names (1-5 words). Move long explanations into child nodes.
3. Structure should have a root node with children, each child can have its own children (max 3 levels deep).
4. Cover all major topics from the study guide.
5. Do NOT include any text outside the JSON.

Required JSON structure:
{{
  "nodeData": {{
    "id": "root",
    "topic": "<Main Topic>",
    "children": [
      {{
        "id": "<unique_id>",
        "topic": "<Short Node Name>",
        "children": [
          {{
            "id": "<unique_id>",
            "topic": "<Subtopic>",
            "children": []
          }}
        ]
      }}
    ]
  }}
}}

Study Guide:
\"\"\"
{study_guide_text}
\"\"\"

Output the Mind Map as JSON only, fully compatible with MindElixir.
""")


def generate_mind_map(study_guide: str) -> str:
    """
    Generate a MindElixir-compatible mind map JSON from a study guide.
    
    Args:
        study_guide: The study guide text to convert into a mind map
        
    Returns:
        JSON string of the mind map in MindElixir format
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
"""
Simple RAGAS Evaluation Script
"""

import json
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from dotenv import load_dotenv

load_dotenv()

# Load your dataset
with open("evaluation/datasets/ragas_evaluation_dataset-2.json", "r") as f:
    data = json.load(f)

# Convert to RAGAS format
dataset = Dataset.from_dict({
    "question": [item["question"] for item in data],
    "answer": [item["answer"] for item in data],
    "contexts": [item["contexts"] for item in data],
    "reference": [item["reference"] for item in data],
})

# Set up evaluator (using GPT-4 for evaluation)
llm = ChatOpenAI(model="gpt-4o", temperature=0)
embeddings = OpenAIEmbeddings(model="text-embedding-3-large")

# Run evaluation
results = evaluate(
    dataset=dataset,
    metrics=[
        faithfulness, 
        answer_relevancy, 
        context_precision, 
        context_recall
    ],
    llm=llm,
    embeddings=embeddings,
)

# Convert to DataFrame first
df = results.to_pandas()

# Save to CSV
df.to_csv("evaluation/datasets/results.csv", index=False)
print("\n✅ Detailed results saved to results.csv")
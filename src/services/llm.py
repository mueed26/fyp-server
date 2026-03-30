from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from src.config.index import appConfig

openAI = {
    "embeddings_llm": ChatOpenAI(
        model="gpt-4o", api_key=appConfig["openai_api_key"], temperature=0
    ),
    "embeddings": OpenAIEmbeddings(
        model="text-embedding-3-large",
        api_key=appConfig["openai_api_key"],
        dimensions=1536,  # ! Do not changes this value. It is used in the document_chunks embedding vector.
    ),
    "chat_llm": ChatOpenAI(
        model="gpt-4o", api_key=appConfig["openai_api_key"], temperature=0
    ),
}
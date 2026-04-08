from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from src.config.index import appConfig

openAI = {
    "embeddings_llm": ChatOpenAI(
        model="gpt-4o",
        api_key=appConfig["openai_api_key"],
        temperature=0,
    ),
    "embeddings": OpenAIEmbeddings(
        model="text-embedding-3-large",
        api_key=appConfig["openai_api_key"],
        dimensions=1536,
    ),
    "chat_llm": ChatOpenAI(
        model="gpt-4o",
        api_key=appConfig["openai_api_key"],
        temperature=0,
    ),
    "features_llm": ChatOpenAI(
        model="gpt-4o",
        api_key=appConfig["openai_api_key"],
        temperature=0,
    ),
}


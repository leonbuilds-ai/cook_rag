"""Use local Ollama natively; support OpenAI-compatible cloud or local embeddings."""
from urllib.parse import urlparse
from langchain_ollama import OllamaEmbeddings, ChatOllama
from langchain_openai import OpenAIEmbeddings, ChatOpenAI


def native_url(url):
    return url.rstrip('/').removesuffix('/v1')


def is_local_ollama(url):
    parsed = urlparse(url)
    return parsed.hostname in {'localhost', '127.0.0.1', '::1'} and parsed.port == 11434


def create_embeddings(config):
    if config.embedding_provider == 'ollama' or is_local_ollama(config.embedding_base_url):
        return OllamaEmbeddings(model=config.embedding_model, base_url=native_url(config.embedding_base_url),
                                num_ctx=config.embedding_num_ctx)
    return OpenAIEmbeddings(model=config.embedding_model, api_key=config.embedding_api_key,
                            base_url=config.embedding_base_url, check_embedding_ctx_length=False,
                            chunk_size=config.embedding_batch_size, request_timeout=120)


def create_llm(config):
    if config.llm_provider == 'ollama' and is_local_ollama(config.llm_base_url):
        return ChatOllama(model=config.llm_model, base_url=native_url(config.llm_base_url),
                          temperature=config.temperature, num_predict=config.max_tokens,
                          num_ctx=config.ollama_num_ctx, reasoning=False)
    return ChatOpenAI(model=config.llm_model, api_key=config.llm_api_key, base_url=config.llm_base_url,
                      temperature=config.temperature, max_tokens=config.max_tokens, timeout=180)

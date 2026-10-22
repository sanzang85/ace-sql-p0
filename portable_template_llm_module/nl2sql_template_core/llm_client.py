from __future__ import annotations

from .intent_contracts import accuracy_mode

import json
import os

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from .config import (
    USE_VLLM_EMBEDDINGS,
    VLLM_API_KEY,
    VLLM_CHAT_MODEL,
    VLLM_EMBEDDING_MODEL,
    VLLM_EMBEDDING_OPENAI_BASE_URL,
    VLLM_MAX_TOKENS,
    VLLM_OPENAI_BASE_URL,
    VLLM_RERANK_MODEL,
)

SAFE_LLM_MAX_TOKENS = max(256, min(VLLM_MAX_TOKENS, 8192 if accuracy_mode() else 1024))


def _load_extra_body() -> dict:
    raw = os.getenv("VLLM_EXTRA_BODY_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def get_chat_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=VLLM_OPENAI_BASE_URL,
        api_key=VLLM_API_KEY,
        model=VLLM_CHAT_MODEL,
        max_tokens=SAFE_LLM_MAX_TOKENS,
        temperature=0.1,
        extra_body=_load_extra_body(),
    )


def get_rerank_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=VLLM_OPENAI_BASE_URL,
        api_key=VLLM_API_KEY,
        model=VLLM_RERANK_MODEL,
        max_tokens=SAFE_LLM_MAX_TOKENS,
        temperature=0.1,
        extra_body=_load_extra_body(),
    )


def get_embeddings() -> OpenAIEmbeddings | None:
    if not USE_VLLM_EMBEDDINGS:
        return None
    return OpenAIEmbeddings(
        base_url=VLLM_EMBEDDING_OPENAI_BASE_URL,
        api_key=VLLM_API_KEY,
        model=VLLM_EMBEDDING_MODEL,
        # Qwen/vLLM must tokenize the original text with its own tokenizer.
        # OpenAI's fallback tiktoken IDs encode a different vocabulary.
        check_embedding_ctx_length=False,
    )


llm = get_chat_llm()
llm_rerank = get_rerank_llm()
embeddings = get_embeddings()

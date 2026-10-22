from __future__ import annotations

from typing import List

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS

from .config import USE_VLLM_EMBEDDINGS
from .llm_client import embeddings
from .text_utils import chunk_text, keyword_top_documents

_EMBEDDING_RUNTIME_AVAILABLE = True
_EMBEDDING_WARNING_PRINTED = False


def _keyword_fallback(query: str, docs: list[Document], k: int) -> list[str]:
    top_docs = keyword_top_documents(query, docs, k=k)
    return [doc.page_content for doc in top_docs]


def retrieve_top_documents(query: str, docs: list[Document], k: int = 6) -> list[Document]:
    global _EMBEDDING_RUNTIME_AVAILABLE
    global _EMBEDDING_WARNING_PRINTED

    if not docs:
        return []

    if not USE_VLLM_EMBEDDINGS or embeddings is None or not _EMBEDDING_RUNTIME_AVAILABLE:
        return keyword_top_documents(query, docs, k=min(k, len(docs)))

    try:
        vectorstore = FAISS.from_documents(docs, embeddings)
        return vectorstore.similarity_search(query, k=min(k, len(docs)))
    except Exception as exc:
        _EMBEDDING_RUNTIME_AVAILABLE = False
        if not _EMBEDDING_WARNING_PRINTED:
            print(f" [retrieval warning] vLLM embeddings unavailable, switching to keyword retrieval for the rest of this run: {exc}")
            _EMBEDDING_WARNING_PRINTED = True
        return keyword_top_documents(query, docs, k=min(k, len(docs)))


def retrieve_top_chunks(query: str, text: str, k: int = 6) -> List[str]:
    chunks = chunk_text(text)
    if not chunks:
        return []

    docs = [Document(page_content=chunk, metadata={"idx": idx}) for idx, chunk in enumerate(chunks)]
    top_docs = retrieve_top_documents(query, docs, k=k)
    return [doc.page_content for doc in top_docs]

"""
Ask a question against the local FAISS index (built by ingest.py).

Flow:
  load embeddings → load FAISS → retrieve top-k → prompt Gemini → answer + sources

Used by the Streamlit app and as a CLI:

    python ask.py "What is the Sandia inverter model?"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import config as cfg

_PROMPT = """You are an AI assistant that answers questions ONLY using the provided context.

Rules:
1. Answer only from the provided context.
2. If the answer is not in the context, say:
   "I couldn't find that information in the uploaded documents."
3. Do not make up facts.
4. Be clear and concise.

Context:
{context}

Question:
{input}

Answer:
"""

# Lazy-loaded so Streamlit can import this module before the index exists
_pipeline = None


def _format_sources(documents) -> list[dict]:
    seen = set()
    sources = []
    for document in documents:
        metadata = getattr(document, "metadata", {}) or {}
        file = os.path.basename(str(metadata.get("source", "Unknown")))
        page = metadata.get("page", "Unknown")
        if isinstance(page, int):
            page = page + 1  # pypdf is 0-indexed
        key = (file, page)
        if key in seen:
            continue
        seen.add(key)
        sources.append({"file": file, "page": page})
    return sources


def index_ready() -> bool:
    return (Path(cfg.VECTORSTORE_DIR) / "index.faiss").exists()


def build_pipeline():
    """Create the retrieval + LLM chain once and reuse it."""
    if not index_ready():
        raise FileNotFoundError(
            f"No FAISS index at {cfg.VECTORSTORE_DIR}. Run: python ingest.py --skip-fabric"
        )

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GOOGLE_API_KEY is not set. Add it to solar_rag/.env (see .env.example)."
        )

    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_community.vectorstores import FAISS
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_classic.chains import create_retrieval_chain
    from langchain_classic.chains.combine_documents import create_stuff_documents_chain

    embeddings = HuggingFaceEmbeddings(model_name=cfg.EMBEDDING_MODEL)
    vectorstore = FAISS.load_local(
        str(cfg.VECTORSTORE_DIR),
        embeddings,
        allow_dangerous_deserialization=True,
    )
    retriever = vectorstore.as_retriever(search_kwargs={"k": cfg.TOP_K})

    llm = ChatGoogleGenerativeAI(model=cfg.LLM_MODEL, google_api_key=api_key)
    prompt = ChatPromptTemplate.from_template(_PROMPT)
    document_chain = create_stuff_documents_chain(llm, prompt)
    return create_retrieval_chain(retriever, document_chain)


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        _pipeline = build_pipeline()
    return _pipeline


def ask(question: str) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("Question must not be empty.")

    chain = get_pipeline()
    response = chain.invoke({"input": question})
    return {
        "answer": response["answer"],
        "sources": _format_sources(response.get("context") or []),
    }


def main() -> None:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        raise SystemExit('Usage: python ask.py "your question"')

    result = ask(question)
    print(result["answer"])
    if result["sources"]:
        print("\nSources:")
        for source in result["sources"]:
            print(f"  - {source['file']} (page {source['page']})")


if __name__ == "__main__":
    main()

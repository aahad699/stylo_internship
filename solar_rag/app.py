"""
Streamlit chat UI for solar_rag.

    cd solar_rag
    streamlit run app.py
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

# Bridge Streamlit Cloud secrets → env (same pattern as the inspiration app)
try:
    if "GOOGLE_API_KEY" in st.secrets and not os.getenv("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = st.secrets["GOOGLE_API_KEY"]
except Exception:
    pass

st.set_page_config(page_title="Solar RAG", page_icon="☀️", layout="wide")
st.title("Solar RAG")
st.caption("Ask questions over your documents. Backed by FAISS locally; Fabric OneLake for durable storage.")

has_gemini = bool(os.getenv("GOOGLE_API_KEY"))
if not has_gemini:
    st.warning(
        "**GOOGLE_API_KEY is not set** — running in retrieval-only mode "
        "(top passages, no Gemini summary). Add the key to `solar_rag/.env` "
        "or Streamlit Secrets for full RAG answers."
    )


@st.cache_resource(show_spinner=False)
def load_ask_module():
    from ask import ask, index_ready

    if not index_ready():
        with st.spinner("Building the knowledge index (first run)…"):
            from ingest import main as build_index
            import sys

            argv = sys.argv
            sys.argv = ["ingest.py", "--skip-fabric"]
            try:
                build_index()
            finally:
                sys.argv = argv
    return ask


ask_fn = load_ask_module()

if "messages" not in st.session_state:
    st.session_state.messages = []


def _answer(question: str) -> dict:
    with st.spinner("Thinking…"):
        return ask_fn(question)


# Optional one-shot demo: open http://localhost:8501/?demo=sandia
if st.query_params.get("demo") == "sandia" and not st.session_state.get("_demo_ran"):
    demo_q = "What is the Sandia inverter performance model used for?"
    result = _answer(demo_q)
    st.session_state.messages = [
        {"role": "user", "content": demo_q},
        {
            "role": "assistant",
            "content": result["answer"],
            "sources": result["sources"],
        },
    ]
    st.session_state._demo_ran = True

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            sources = message.get("sources") or []
            if sources:
                st.markdown("**Sources**")
                for source in sources:
                    st.caption(f"{source['file']} (page {source['page']})")

question = st.chat_input("Ask anything about the indexed documents…")

if question:
    result = _answer(question)
    st.session_state.messages.append({"role": "user", "content": question})
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": result["answer"],
            "sources": result["sources"],
        }
    )
    st.rerun()

with st.sidebar:
    st.subheader("Index")
    vs = Path(__file__).resolve().parent / "vectorstore" / "index.faiss"
    st.write("FAISS ready:" , "✅" if vs.exists() else "❌")
    st.caption("Re-ingest after adding PDFs: `python ingest.py`")
    st.subheader("Fabric")
    import config as cfg

    configured = bool(cfg.FABRIC_LAKEHOUSE_ID) and "REPLACE" not in cfg.FABRIC_LAKEHOUSE_ID
    st.write("Lakehouse id set:", "✅" if configured else "❌ (create SolarRAG, paste id)")
    st.caption(f"Workspace: `{cfg.FABRIC_WORKSPACE_ID}`")
    st.caption(f"Lakehouse name: `{cfg.FABRIC_LAKEHOUSE_NAME}`")

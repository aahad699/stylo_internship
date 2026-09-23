# Solar RAG

General-purpose Retrieval-Augmented Generation app that lives under `solar_rag/`.
It indexes PDFs, answers questions with grounded citations, and stores the chunk
table in a **Microsoft Fabric** lakehouse.

Inspired by [langchain-rag-assistant](https://github.com/aahad699/langchain-rag-assistant):
LangChain, local HuggingFace embeddings, Gemini, FAISS.

## Architecture

```
Ingest (python ingest.py)

  data/*.pdf  →  load  →  chunk  →  embed (BGE-base)  →  FAISS (vectorstore/)
                                              ↘
                                    Fabric sync
                                      • Tables/dbo/solar_rag_chunks

Query (python ask.py "…")

  question → SELECT dbo.solar_rag_chunks from the SQL endpoint
           → keep the 4 closest rows → Gemini → answer + sources
```

## How Fabric fits in

| Artifact | Where it lives |
|----------|----------------|
| PDFs and the FAISS index | On this laptop (`data/`, `vectorstore/`) |
| Chunks + embeddings | Delta table `dbo.solar_rag_chunks` |

**Fabric targets** (set at the top of `ingest.py` and `ask.py`):

| | GUID |
|--|--|
| Workspace | `1aa55571-424f-4f72-ab00-18ec4ccb6cfb` |
| Lakehouse | `8f082de5-0870-4e46-bc07-2ff0a4e6134e` |

`python ingest.py` writes the local FAISS index and `dbo.solar_rag_chunks`.
Sign in with `az login` before that command. `ask.py` uses the same login to query the SQL endpoint.

## Quick start

```bash
cd solar_rag
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # put GOOGLE_API_KEY in .env

az login
python ingest.py                   # local FAISS index + dbo.solar_rag_chunks
python ask.py "What does the Sandia inverter performance model predict?"
```

`ask.py` reads `dbo.solar_rag_chunks` through the lakehouse SQL analytics endpoint. Sign in with `az login` first. Without `GOOGLE_API_KEY`, it prints the four matching passages. Set the key for a Gemini answer.

## Documents

Seed PDFs under `data/` (see `data/SOURCES.md`) — Sandia inverter model + arXiv PV / irradiance / smart-inverter papers. Drop any other PDFs into `data/` and re-run ingest.

## Layout

```
solar_rag/
├── ask.py              # SQL query, then Gemini
├── ingest.py           # PDFs → FAISS, then the chunk table
├── data/               # source PDFs
├── vectorstore/        # local FAISS (gitignored)
├── requirements.txt
└── .env.example
```

## LLM choice

**Google Gemini `gemini-2.5-flash`** for generation (free-tier API key, same as the inspiration project) and **local `BAAI/bge-base-en-v1.5`** embeddings (no embedding API cost). Set `GOOGLE_API_KEY` in `.env`.

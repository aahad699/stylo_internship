# Solar RAG

General-purpose Retrieval-Augmented Generation app that lives under `solar_rag/`.
It indexes PDFs, answers questions with grounded citations, and uses **Microsoft Fabric
OneLake** as the durable storage medium (docs + FAISS files + chunk Delta table).

Inspired by [langchain-rag-assistant](https://github.com/aahad699/langchain-rag-assistant):
Streamlit chat, LangChain, local HuggingFace embeddings, Gemini, FAISS.

## Architecture

```
Ingest (python ingest.py)

  data/*.pdf  →  load  →  chunk  →  embed (MiniLM)  →  FAISS (vectorstore/)
                                              ↘
                                    optional Fabric sync
                                      • Files/solar_rag/docs/
                                      • Files/solar_rag/vectorstore/
                                      • Tables/dbo/solar_rag_chunks

Query (streamlit run app.py  |  python ask.py "…")

  question → retrieve top-k → Gemini (context only) → answer + sources
```

## How Fabric fits in

| Artifact | OneLake location |
|----------|------------------|
| Source PDFs | `Files/solar_rag/docs/` |
| FAISS index | `Files/solar_rag/vectorstore/` |
| Chunks + embeddings | Delta table `dbo.solar_rag_chunks` |

**Create a new lakehouse** (do this once in your Fabric workspace):

1. Open the Fabric portal → your workspace (same one the VS Code Fabric extension is signed into).
2. **New** → **Lakehouse** → name it `SolarRAG`.
3. Open the lakehouse → **Settings / About** → copy the **Lakehouse ID** (GUID).
4. Paste it into `config.py` as `FABRIC_LAKEHOUSE_ID`.
5. Workspace id is already set to the same value used by `sales_predictor`.

**Two ways to sync / read OneLake** (same pattern as `sales_predictor`):

| Mode | When | How |
|------|------|-----|
| Fabric Runtime | VS Code **Microsoft Fabric Runtime** kernel / portal | `spark` / `mssparkutils` talk to the lakehouse by name |
| Local Python | laptop / this repo `.venv` | `az login` (or service principal) + `deltalake` / Azure Data Lake SDK over `abfss://…@onelake.dfs.fabric.microsoft.com` |

Until the lakehouse id is pasted, `ingest.py` still builds a fully working **local** index; Fabric sync is skipped with a clear message.

## Quick start

```bash
cd solar_rag
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # put GOOGLE_API_KEY in .env

python ingest.py --skip-fabric     # build local FAISS from data/*.pdf
streamlit run app.py               # chat UI

# after SolarRAG lakehouse id is in config.py:
python ingest.py                   # local rebuild + push to OneLake
```

CLI ask without the UI:

```bash
python ask.py "What does the Sandia inverter performance model predict?"
```

Without `GOOGLE_API_KEY`, ask/app still run in **retrieval-only** mode (top passages + sources). Set the key for Gemini-written answers.

Demo URL (auto-asks the Sandia question once): `http://localhost:8501/?demo=sandia`

## Documents

Seed PDFs under `data/` (see `data/SOURCES.md`) — Sandia inverter model + arXiv PV / irradiance / smart-inverter papers. Drop any other PDFs into `data/` and re-run ingest.

## Layout

```
solar_rag/
├── app.py              # Streamlit chat
├── ask.py              # retrieve → Gemini → answer
├── ingest.py           # PDFs → FAISS (+ Fabric sync)
├── fabric_store.py     # OneLake Files + Delta helpers
├── config.py           # models, paths, Fabric ids
├── data/               # source PDFs
├── vectorstore/        # local FAISS (gitignored)
├── requirements.txt
└── .env.example
```

## LLM choice

**Google Gemini `gemini-2.5-flash`** for generation (free-tier API key, same as the inspiration project) and **local `all-MiniLM-L6-v2`** embeddings (no embedding API cost). Set `GOOGLE_API_KEY` in `.env`.

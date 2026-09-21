"""Paths and knobs used by ingest / ask / Fabric sync.

Edit FABRIC_* after you create the SolarRAG lakehouse in your workspace.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent

DATA_DIR = ROOT / "data"
VECTORSTORE_DIR = ROOT / "vectorstore"
CHUNKS_PARQUET = ROOT / "vectorstore" / "chunks.parquet"

# Embedding + LLM (Gemini is free-tier friendly and matches the inspiration RAG)
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
LLM_MODEL = "gemini-2.5-flash"

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
TOP_K = 4

# --- Microsoft Fabric / OneLake (new SolarRAG lakehouse) ---
# Same workspace as sales_predictor. Create a lakehouse named SolarRAG in Fabric,
# then paste its lakehouse GUID below (Lakehouse settings → About).
FABRIC_WORKSPACE_ID = "1dca1c65-3ce2-483e-a7fb-baf7fca28e9f"
FABRIC_LAKEHOUSE_ID = "REPLACE_WITH_SOLAR_RAG_LAKEHOUSE_ID"
FABRIC_LAKEHOUSE_NAME = "SolarRAG"
FABRIC_SCHEMA = "dbo"
FABRIC_CHUNKS_TABLE = "solar_rag_chunks"

# OneLake paths under the lakehouse
FABRIC_DOCS_FILES_PREFIX = "Files/solar_rag/docs"
FABRIC_VECTORSTORE_FILES_PREFIX = "Files/solar_rag/vectorstore"

# Sync to Fabric after local ingest when True and lakehouse id is set
FABRIC_SYNC_ENABLED = True

"""Paths and knobs used by ingest / ask / Fabric sync."""

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

# --- Microsoft Fabric / OneLake (dedicated solar_rag workspace) ---
# From: https://app.fabric.microsoft.com/groups/<workspace>/lakehouses/<lakehouse>
# If you rename the lakehouse in Fabric, update FABRIC_LAKEHOUSE_NAME to match
# (needed for Spark saveAsTable; local delta-rs sync uses the GUID only).
FABRIC_WORKSPACE_ID = "1aa55571-424f-4f72-ab00-18ec4ccb6cfb"
FABRIC_LAKEHOUSE_ID = "8f082de5-0870-4e46-bc07-2ff0a4e6134e"
FABRIC_LAKEHOUSE_NAME = "SolarRAG"
FABRIC_SCHEMA = "dbo"
FABRIC_CHUNKS_TABLE = "solar_rag_chunks"

# OneLake paths under the lakehouse
FABRIC_DOCS_FILES_PREFIX = "Files/solar_rag/docs"
FABRIC_VECTORSTORE_FILES_PREFIX = "Files/solar_rag/vectorstore"

# Sync to Fabric after local ingest when True and lakehouse id is set
FABRIC_SYNC_ENABLED = True

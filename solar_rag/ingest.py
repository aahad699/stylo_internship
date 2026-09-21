"""
Build the local FAISS index from PDFs in data/, then optionally sync to Fabric.

Flow (top to bottom):
  1. load PDFs
  2. split into chunks
  3. embed with HuggingFace MiniLM
  4. build + save FAISS
  5. (optional) push docs / index / chunk table to SolarRAG lakehouse

    python ingest.py
    python ingest.py --skip-fabric
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import config as cfg


def _clean_text(text: str) -> str:
    """Drop unpaired surrogates / nulls that break the tokenizer (common in PDF math fonts)."""
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))
    return cleaned.replace("\x00", "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest PDFs into FAISS (+ Fabric).")
    parser.add_argument(
        "--skip-fabric",
        action="store_true",
        help="Only build the local vectorstore; do not sync to OneLake.",
    )
    args = parser.parse_args()

    data_dir = Path(cfg.DATA_DIR)
    pdf_files = sorted(data_dir.glob("*.pdf"))
    if not pdf_files:
        raise SystemExit(f"No PDFs found in {data_dir}. Add documents and re-run.")

    # --- load ---
    from langchain_community.document_loaders import PyPDFLoader

    print(f"Loading {len(pdf_files)} PDF(s) from {data_dir} …")
    documents = []
    for pdf in pdf_files:
        pages = PyPDFLoader(str(pdf)).load()
        documents.extend(pages)
        print(f"  {pdf.name}: {len(pages)} page(s)")
    print(f"Loaded {len(documents)} page(s) total.")

    # --- split ---
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=cfg.CHUNK_SIZE,
        chunk_overlap=cfg.CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(documents)
    cleaned = []
    for chunk in chunks:
        text = _clean_text(chunk.page_content)
        if len(text) < 40:
            continue
        chunk.page_content = text
        cleaned.append(chunk)
    chunks = cleaned
    if not chunks:
        raise SystemExit("No text could be extracted from the PDFs.")
    print(f"Split into {len(chunks)} usable chunk(s).")

    # --- embed + FAISS ---
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_community.vectorstores import FAISS

    print(f"Loading embedding model {cfg.EMBEDDING_MODEL} (first run downloads) …")
    embeddings = HuggingFaceEmbeddings(model_name=cfg.EMBEDDING_MODEL)

    print("Building FAISS index …")
    vectorstore = FAISS.from_documents(chunks, embeddings)

    cfg.VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)
    vectorstore.save_local(str(cfg.VECTORSTORE_DIR))
    print(f"Saved FAISS index → {cfg.VECTORSTORE_DIR}")

    # Materialise chunk rows for Fabric Delta + local parquet (debug / reload)
    print("Materialising chunk rows (text + embeddings) …")
    texts = [c.page_content for c in chunks]
    vectors = embeddings.embed_documents(texts)
    chunk_rows = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        meta = chunk.metadata or {}
        chunk_rows.append(
            {
                "chunk_id": i,
                "text": chunk.page_content,
                "source": os.path.basename(str(meta.get("source", ""))),
                "page": int(meta["page"]) if isinstance(meta.get("page"), int) else meta.get("page"),
                "embedding": vector,
            }
        )

    import pandas as pd

    parquet_rows = [
        {**row, "embedding": json.dumps(row["embedding"])} for row in chunk_rows
    ]
    pd.DataFrame(parquet_rows).to_parquet(cfg.CHUNKS_PARQUET, index=False)
    print(f"Saved chunk table → {cfg.CHUNKS_PARQUET}")

    # --- Fabric sync (docs + FAISS files + Delta chunks) ---
    skip_fabric = args.skip_fabric or not cfg.FABRIC_SYNC_ENABLED
    if skip_fabric:
        print("Skipping Fabric sync (--skip-fabric or FABRIC_SYNC_ENABLED=False).")
        print("Done.")
        return

    from fabric_store import lakehouse_configured, sync_local_artifacts

    if not lakehouse_configured():
        print(
            "Fabric lakehouse id not set yet (config.FABRIC_LAKEHOUSE_ID).\n"
            "Create lakehouse 'SolarRAG' in Fabric, paste the id into config.py, then re-run\n"
            "  python ingest.py\n"
            "Local index is ready either way."
        )
        print("Done.")
        return

    print("Syncing docs + vectorstore + chunks table to OneLake …")
    summary = sync_local_artifacts(chunk_rows=chunk_rows)
    print(f"Fabric sync complete: {summary}")
    print("Done.")


if __name__ == "__main__":
    main()

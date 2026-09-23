# Load the PDFs in data/, embed each chunk once, save a FAISS index,
# then write those same chunks to dbo.solar_rag_chunks.
#
#   python ingest.py

#-- project folders --
from pathlib import Path

#-- read .env --
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
VECTORSTORE_DIR = ROOT / "vectorstore"

EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
QUERY_PROMPT = "Represent this sentence for searching relevant passages: "
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

WORKSPACE_ID = "1aa55571-424f-4f72-ab00-18ec4ccb6cfb"
LAKEHOUSE_ID = "8f082de5-0870-4e46-bc07-2ff0a4e6134e"
TABLE_PATH = (
    f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/"
    f"{LAKEHOUSE_ID}/Tables/dbo/solar_rag_chunks"
)

pdf_files = sorted(DATA_DIR.glob("*.pdf"))
if not pdf_files:
    raise SystemExit(f"No PDFs found in {DATA_DIR}")

#-- load every PDF in data/ --
from langchain_community.document_loaders import PyPDFLoader

print(f"Loading {len(pdf_files)} PDF(s) from {DATA_DIR}")
documents = []
for pdf in pdf_files:
    pages = PyPDFLoader(str(pdf)).load()
    documents.extend(pages)
    print(f"  {pdf.name}: {len(pages)} page(s)")
print(f"Loaded {len(documents)} page(s)")

#-- split pages into overlapping chunks and drop the short scraps --
from langchain_text_splitters import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
)
chunks = []
for chunk in splitter.split_documents(documents):
    text = chunk.page_content or ""
    text = "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))
    text = text.replace("\x00", "").strip()
    if len(text) < 40:
        continue
    chunk.page_content = text
    chunks.append(chunk)
if not chunks:
    raise SystemExit("No text could be extracted from the PDFs.")
print(f"Split into {len(chunks)} usable chunk(s)")

try:
    #-- allow the embedding model download on this laptop --
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

#-- embed each chunk once and save the FAISS index --
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings

print(f"Loading embedding model {EMBEDDING_MODEL}")
embeddings = HuggingFaceEmbeddings(
    model_name=EMBEDDING_MODEL,
    encode_kwargs={"normalize_embeddings": True},
    query_encode_kwargs={"normalize_embeddings": True, "prompt": QUERY_PROMPT},
)
texts = [chunk.page_content for chunk in chunks]
vectors = embeddings.embed_documents(texts)
vectorstore = FAISS.from_embeddings(
    text_embeddings=list(zip(texts, vectors)),
    embedding=embeddings,
    metadatas=[chunk.metadata for chunk in chunks],
)
VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)
vectorstore.save_local(str(VECTORSTORE_DIR))
print(f"Saved FAISS index -> {VECTORSTORE_DIR}")

#-- pack the vectors and write dbo.solar_rag_chunks --
import base64
import os
import struct

import pandas as pd
from azure.identity import AzureCliCredential
from deltalake import write_deltalake

rows = []
for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
    meta = chunk.metadata or {}
    page = meta.get("page")
    values = [float(item) for item in vector]
    raw = struct.pack(f"<{len(values)}f", *values)
    rows.append(
        {
            "chunk_id": i,
            "text": chunk.page_content,
            "source": os.path.basename(str(meta.get("source", ""))),
            "page": int(page) if isinstance(page, int) else page,
            "embedding": base64.b64encode(raw).decode("ascii"),
        }
    )

print("Writing dbo.solar_rag_chunks to OneLake")
token = AzureCliCredential().get_token("https://storage.azure.com/.default").token
write_deltalake(
    TABLE_PATH,
    pd.DataFrame(rows),
    mode="overwrite",
    schema_mode="overwrite",
    storage_options={"bearer_token": token, "use_fabric_endpoint": "true"},
)
print("Done.")

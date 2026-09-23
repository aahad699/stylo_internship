# Load the PDFs in data/, embed each chunk once,
# then write those chunks to dbo.solar_rag_chunks.
#   python ingest.py

#-- project folders --
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

pdf_files = sorted(DATA_DIR.glob("*.pdf"))
if not pdf_files:
    raise SystemExit(f"No PDFs found in {DATA_DIR}")

#-- load every PDF in data/ --
from langchain_community.document_loaders import PyPDFLoader
print(f"Loading {len(pdf_files)} PDF(s) from {DATA_DIR}")
documents = [] #stores all the 'pages' from all the PDFs
for pdf in pdf_files:
    pages = PyPDFLoader(str(pdf)).load()
    documents.extend(pages)
    print(f"  {pdf.name}: {len(pages)} page(s)")
print(f"Loaded {len(documents)} page(s)")

#-- split pages into overlapping chunks and drop the short scraps --
from langchain_text_splitters import RecursiveCharacterTextSplitter
splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
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

#-- embed each chunk once --
from langchain_huggingface import HuggingFaceEmbeddings
embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-base-en-v1.5",
    encode_kwargs={"normalize_embeddings": True},
)
texts = [chunk.page_content for chunk in chunks]
vectors = embeddings.embed_documents(texts)

#-- pack the vectors and write dbo.solar_rag_chunks --
import base64
import struct
import pandas as pd
from azure.identity import AzureCliCredential
from deltalake import write_deltalake
rows = []
for chunk, vector in zip(chunks, vectors):
    raw = struct.pack(f"<{len(vector)}f", *vector)
    rows.append({
        "text": chunk.page_content,
        "embedding": base64.b64encode(raw).decode("ascii"),
    })

print("Writing dbo.solar_rag_chunks to OneLake")
token = AzureCliCredential().get_token("https://storage.azure.com/.default").token
WORKSPACE_ID = "1aa55571-424f-4f72-ab00-18ec4ccb6cfb"
LAKEHOUSE_ID = "8f082de5-0870-4e46-bc07-2ff0a4e6134e"
TABLE_PATH = (
    f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/"
    f"{LAKEHOUSE_ID}/Tables/dbo/solar_rag_chunks"
)
write_deltalake(
    TABLE_PATH,
    pd.DataFrame(rows),
    mode="overwrite",
    schema_mode="overwrite",
    storage_options={"bearer_token": token, "use_fabric_endpoint": "true"},
)
print("Done.")
# Read dbo.solar_rag_chunks from the SolarRAG SQL endpoint, keep the 4
# closest chunks, and ask Gemini to answer from those chunks only.
#
#   python ask.py "What is the Sandia inverter model?"

import sys

from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
QUERY_PROMPT = "Represent this sentence for searching relevant passages: "
LLM_MODEL = "gemini-2.5-flash"
TOP_K = 4

SQL_SERVER = (
    "wsw7jvfvolau5hwgzi4rno2o6u-ofk2kgspijze7kyaddwezs3m7m"
    ".datawarehouse.fabric.microsoft.com"
)

PROMPT = """You are an AI assistant that answers questions ONLY using the provided context.

Rules:
1. Answer only from the provided context.
2. If the answer is not in the context, say:
   "I couldn't find that information in the uploaded documents."
3. Do not make up facts.
4. Be clear and concise.

Context:
{context}

Question:
{question}

Answer:
"""

question = " ".join(sys.argv[1:]).strip()
if not question:
    raise SystemExit('Usage: python ask.py "your question"')

import struct

import pyodbc
from azure.identity import AzureCliCredential

token = AzureCliCredential().get_token("https://database.windows.net/.default").token
encoded = token.encode("utf-16-le")
token_struct = struct.pack(f"<I{len(encoded)}s", len(encoded), encoded)
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 18 for SQL Server};"
    f"SERVER={SQL_SERVER};"
    "DATABASE=SolarRAG;"
    "Encrypt=yes;"
    "TrustServerCertificate=no;",
    attrs_before={1256: token_struct},
)
cursor = conn.cursor()
cursor.execute(
    "SELECT source, page, text, embedding FROM dbo.solar_rag_chunks"
)
rows = cursor.fetchall()
conn.close()
if not rows:
    raise SystemExit("dbo.solar_rag_chunks returned no rows. Run python ingest.py")

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name=EMBEDDING_MODEL,
    encode_kwargs={"normalize_embeddings": True},
    query_encode_kwargs={"normalize_embeddings": True, "prompt": QUERY_PROMPT},
)
query = embeddings.embed_query(question)

import base64

scored = []
for source, page, text, embedding in rows:
    raw = base64.b64decode(embedding)
    vector = struct.unpack(f"<{len(raw) // 4}f", raw[: (len(raw) // 4) * 4])
    if len(vector) != len(query):
        raise SystemExit("Embedding column looks truncated. Run python ingest.py again.")
    score = sum(a * b for a, b in zip(query, vector))
    scored.append((score, source, page, text))
scored.sort(key=lambda item: item[0], reverse=True)
top = scored[:TOP_K]

context = "\n\n".join(text.strip() for score, source, page, text in top)

import os

api_key = os.getenv("GOOGLE_API_KEY")
if not context:
    print("I couldn't find that information in the uploaded documents.")
elif not api_key:
    print("No GOOGLE_API_KEY. Passages:\n")
    print(context)
else:
    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = ChatGoogleGenerativeAI(model=LLM_MODEL, google_api_key=api_key)
    print(llm.invoke(PROMPT.format(context=context, question=question)).content)

print("\nSources:")
seen = set()
for score, source, page, text in top:
    file = os.path.basename(str(source or "Unknown"))
    if isinstance(page, (int, float)):
        page = int(page) + 1  # pypdf pages start at 0
    if (file, page) in seen:
        continue
    seen.add((file, page))
    print(f"  - {file} (page {page})")

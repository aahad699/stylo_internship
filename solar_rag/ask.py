# Read dbo.solar_rag_chunks from the SolarRAG SQL endpoint, keep the 4
# closest chunks, and ask Gemini to answer from those chunks only.
#   python ask.py "What is the Sandia inverter model?"

#-- read the question from the command line --
import sys
question = " ".join(sys.argv[1:]).strip()
if not question:
    raise SystemExit('Usage: python ask.py "your question"')

#-- read .env --
from dotenv import load_dotenv
load_dotenv()

#-- sign in and SELECT the chunk table from the SQL endpoint --
import struct
import pyodbc
from azure.identity import AzureCliCredential
token = AzureCliCredential().get_token("https://database.windows.net/.default").token
encoded = token.encode("utf-16-le")
token_struct = struct.pack(f"<I{len(encoded)}s", len(encoded), encoded)
SQL_SERVER = (
    "wsw7jvfvolau5hwgzi4rno2o6u-ofk2kgspijze7kyaddwezs3m7m"
    ".datawarehouse.fabric.microsoft.com"
)
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 18 for SQL Server};"
    f"SERVER={SQL_SERVER};"
    "DATABASE=SolarRAG;",
    attrs_before={1256: token_struct},
)
rows = conn.execute("SELECT text, embedding FROM dbo.solar_rag_chunks").fetchall()
conn.close()
if not rows:
    raise SystemExit("dbo.solar_rag_chunks returned no rows. Run python ingest.py")

try:
    #-- allow the embedding model download on this laptop --
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

#-- embed the question with the same model used at ingest --
from langchain_huggingface import HuggingFaceEmbeddings
embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-base-en-v1.5",
    query_encode_kwargs={"normalize_embeddings": True,"prompt": "Represent this sentence for searching relevant passages: "},
)
query = embeddings.embed_query(question)

#-- decode each stored vector and keep the 4 closest chunks --
import base64
scored = []
for text, embedding in rows:
    vector = struct.unpack(f"<{len(query)}f", base64.b64decode(embedding))
    score = sum(a * b for a, b in zip(query, vector))
    scored.append((score, text))
scored.sort(key=lambda item: item[0], reverse=True)
top = scored[:4]
context = "\n\n".join(text for score, text in top)

#-- answer from those chunks, or print them if there is no Gemini key --
import os
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    print("No GOOGLE_API_KEY. Passages:\n")
    print(context)
else:
    #-- ask Gemini using only the chunks above --
    from langchain_google_genai import ChatGoogleGenerativeAI
    LLM_MODEL = "gemini-2.5-flash"
    llm = ChatGoogleGenerativeAI(model=LLM_MODEL, google_api_key=api_key)
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
    print(llm.invoke(PROMPT.format(context=context, question=question)).content)
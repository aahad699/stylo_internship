# Load the PDFs in data/, embed each chunk once,
# then write those chunks to dbo.solar_rag_chunks.
#   python ingest.py

#-- project folders --
from pathlib import Path
from langchain_community.document_loaders import PyPDFLoader
ROOT = Path(__file__).resolve().parent #.resolve() converts a relative path (e.g., "./ingest.py") into a canonical, absolute path (e.g., "C:/Users/hp/stylo_internship/solar_rag/ingest.py"), .parent is needed to get the parent directory and drops the filename.
DATA_DIR = ROOT / "data"
pdf_files = sorted(DATA_DIR.glob("*.pdf")) #this variable stores the list/names of PDF files. sorted() turns that into a list in alphabetical order so runs are repeatable. glob() finds every file matching the pattern
if not pdf_files:
    raise SystemExit(f"No PDFs found in {DATA_DIR}")

#-- load every PDF in data/ --
documents = [] #stores all the 'pages' from all the PDFs
for pdf in pdf_files:
    pages = PyPDFLoader(str(pdf)).load()
    documents.extend(pages)
print(f"Loaded {len(documents)} page(s)")

#-- split pages into overlapping chunks and drop the short scraps --
# It make chunks using RecursiveCharacterTextSplitter, clean it and then store in chunks[].
from langchain_text_splitters import RecursiveCharacterTextSplitter
splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
chunks = []
for chunk in splitter.split_documents(documents):
    text = chunk.page_content #take the chunk's text
    # Create an empty string to store the results
    clean_text = ""
    # Loop through each character in the original text
    for ch in text:
        # This checks if the character's code point falls within the range of UTF-16 surrogates (55,296 to 57,343 in decimal).
        if not (55296 <= ord(ch) and ord(ch) <= 57343):
            # Keep the valid character by adding it to the result string
            clean_text = clean_text + ch
    # Update the original variable with the filtered text
    text = clean_text
    #remove surrogate pairs that can break the embedding model.
    text = text.replace("\x00", "").strip() #remove nulls (\x00) and whitespace
    if len(text) < 40: #drop any chunk that is too short to be useful
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
    encode_kwargs={"normalize_embeddings": True}, #normalize = True means the vector will be scaled to have a length of 1, which is important for cosine similarity calculations.
)
texts = [chunk.page_content for chunk in chunks]
vectors = embeddings.embed_documents(texts) #for each chunk, a list of 768 float numbers

#-- pack the vectors and write dbo.solar_rag_chunks --
import base64 #used to encode binary data into text; dry run: base64.b64encode(b"hello world").decode("ascii") returns 'aGVsbG8gd29ybGQ='
import struct #used to convert Python numerical values into their binary representation; dry run: struct.pack("<3f", 1.0, 2.0, 3.0) returns b'\x00\x00\x80?\x00\x00\x00@\x00\x00@@'
import pandas as pd
from azure.identity import AzureCliCredential
from deltalake import write_deltalake
rows = []
for chunk, vector in zip(chunks, vectors): #zip() combines two lists into pairs, so we can iterate over both chunks and vectors at the same time.
    rows.append({"text": chunk.page_content,"embedding": base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")}) 
    '''Standard JSON arrays of floats (e.g., "[0.123, 0.456, ...]") take up a lot of text space. 
    Packing them into binary and encoding them as Base64 significantly reduces the payload size and speeds up database insertion times. 
    Base64 is chosen because it represents raw binary data using only safe, universally accepted text characters. you get back what you stored by decoding the Base64 string and unpacking the binary data back into floats.
    Delta/Lakehouse tables have no native "vector" type. '''
try:
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
    print("SUCCESS: dbo.solar_rag_chunks written to OneLake.")
except Exception as e:
    print(f"Error occurred while writing to OneLake: {e}")
    raise SystemExit("Failed to write to OneLake.")
# Sales RAG

Ask a question about the Gold warehouse. The script turns the question into one T-SQL SELECT, runs it on the Gold SQL endpoint, and answers from those rows.

```
question → one prompt (schema as context) → SELECT
         → Gold SQL endpoint
         → same prompt (rows as context) → answer
```

## How to run

```bash
cd sales_rag
pip install -r requirements.txt
cp .env.example .env    # set GOOGLE_API_KEY
az login
python ask_nlp.py "What was total revenue in 2020?"
```

Sign in with `az login` first. The SQL endpoint is the Gold mirrored warehouse.

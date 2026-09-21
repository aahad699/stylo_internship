# Stylo Internship

Portfolio repository for internship work at Stylo. It currently holds **three** independent projects; more will be added as the internship progresses.

Each project lives in its own folder with its own notebooks, data (where applicable), and run notes. There is no single shared app stack for the whole repo.

## Projects

| Project | Path | One-liner |
|---|---|---|
| **Sales predictor** | [`sales_predictor/`](sales_predictor/) | Microsoft Fabric / OneLake revenue forecasting from `Gold.dbo.factsales_gold` (multi-model notebook plus a simpler linear + Prophet notebook). |
| **Solar predictor** | [`solar_predictor/`](solar_predictor/) | Predicts solar plant inverter **AC power** from Plant 1 generation and weather sensor CSVs (EDA, cleaning, features, scaled linear regression). |
| **Solar RAG** | [`solar_rag/`](solar_rag/) | General Streamlit RAG (LangChain + FAISS + Gemini) with Microsoft Fabric OneLake as durable storage for docs, index files, and chunk embeddings. |

### `sales_predictor`

Forecasts daily / weekly / monthly sales revenue against a Fabric Gold lakehouse.

- **`Revenue_Forecast_Notebook.ipynb`** — main notebook: reads live `factsales_gold`, benchmarks candidate models (and blends), picks a champion, writes forecast / backtest / history tables, logs to MLflow. Runs on Fabric Spark or locally against OneLake.
- **`revenue_forecast.ipynb`** — earlier notebook: load Gold fact sales, preprocess, linear regression and Prophet-style forecasting / visualization.
- **`README_VSCode.md`** — how to run the Fabric notebook from VS Code (Fabric runtime vs local Python kernel).
- **`requirements-local.txt`** — Python deps for local runs of `Revenue_Forecast_Notebook.ipynb`.
- **`Revenue_Forecasting_Manual.pdf`** — forecasting manual for models and outputs.

Fabric Git sync for the main notebook also lives at the repo root as [`Revenue_Forecast_Notebook.Notebook/`](Revenue_Forecast_Notebook.Notebook/) (`notebook-content.py` + `.platform`).

**Quick local setup** (details in the VS Code guide):

```bash
cd sales_predictor
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-local.txt
az login
```

Open `Revenue_Forecast_Notebook.ipynb`, select the `.venv` kernel, and run cells in order. Prefer the Fabric Data Engineering extension + Microsoft Fabric Runtime when you want production-identical Spark behaviour.

### `solar_predictor`

Offline notebook over bundled CSV data (no Fabric dependency in this folder).

- **`solar_forecast.ipynb`** — load `Plant_1_Generation_Data.csv` and `Plant_1_Weather_Sensor_Data.csv`, inspect and clean, merge on `DATE_TIME`, engineer features (including inverter dummies and time features), scale/split, fit **linear regression** to predict `AC_POWER`, and plot actual vs predicted.
- **`data/`** — the two Plant 1 CSV inputs used by the notebook.

**Quick local setup:**

```bash
cd solar_predictor
python -m venv .venv
source .venv/bin/activate
pip install pandas numpy scikit-learn matplotlib jupyter
jupyter notebook solar_forecast.ipynb
```

(Paths in the notebook expect you to run it with `solar_predictor/` as the working directory so `data/` resolves.)

### `solar_rag`

General RAG chat over PDFs. Local FAISS + HuggingFace embeddings + Gemini; Fabric lakehouse **SolarRAG** stores docs, the FAISS files, and a Delta chunk table. See [`solar_rag/README.md`](solar_rag/README.md).

```bash
cd solar_rag
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set GOOGLE_API_KEY
python ingest.py --skip-fabric
streamlit run app.py
```

## Repository layout

```
README.md
.gitignore
sales_predictor/                 # Fabric sales revenue forecasting
solar_predictor/                 # Solar AC power prediction (CSV + notebook)
solar_rag/                       # Streamlit RAG + Fabric OneLake storage
Revenue_Forecast_Notebook.Notebook/   # Fabric-synced source for the sales notebook
```

## Credentials and local artifacts

Do not commit secrets, Azure / Fabric tokens, local virtualenvs, MLflow runs, or exported lakehouse dumps. See [`.gitignore`](.gitignore).

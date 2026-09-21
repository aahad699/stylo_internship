# Sales Revenue Forecast

Microsoft Fabric notebook that forecasts sales revenue from the Gold lakehouse. It reads the live `Gold.dbo.factsales_gold` Delta table, benchmarks candidate forecasting models, selects a champion by weekly sMAPE, and writes daily, weekly, monthly, backtest, and forecast-history tables back to OneLake. Runs, metrics, and models are logged to the MLflow experiment `sales_revenue_forecast`.

This repository is the internship / Stylo project deliverable for that forecasting pipeline (`stylo_internship` on GitHub).

## What it does

1. Builds a daily revenue series from fact sales (`Quantity × UnitPrice + Tax` when `INCLUDE_TAX` is enabled).
2. Benchmarks up to ~25 candidates (single models plus blends; XGBoost / CatBoost candidates are skipped if those libraries are missing) with rolling-origin backtests.
3. Scores weekly and monthly totals (MAE, RMSE, MAPE, sMAPE, WAPE, accuracy, bias, R², and direction-of-change precision / recall / F1).
4. Picks the champion (lowest average weekly sMAPE), retrains on full history, and forecasts the rest of the current month plus `HORIZON_MONTHS` full months (default 3).
5. Writes result tables to the Gold lakehouse and logs to MLflow (Fabric workspace experiment when available; otherwise local `./mlruns`).

### Output tables

| Table | Purpose |
|---|---|
| `revenue_forecast_gold` | Daily forecast from the latest run |
| `revenue_weekly_gold` | Actual + forecast revenue per ISO week |
| `revenue_monthly_gold` | Actual + forecast revenue per month |
| `forecast_backtest_gold` | Benchmark metrics per model / fold / grain |
| `revenue_forecast_history_gold` | Append-only monthly forecast history across runs |

### Candidate models (when libraries are available)

Baselines and classical: naive last week, 4-week moving average, Holt damped trend, Prophet.

ML (lagged weekly log-growth features): linear regression, ridge, random forest, gradient boosting, LightGBM, XGBoost, CatBoost.

Blends: simple averages of selected component forecasts (for example Prophet + Holt, boosting ensembles, and a grand blend).

## Repository layout

```
README.md
.gitignore
files/
  Revenue_Forecast_Notebook.ipynb   # notebook for VS Code / local or Fabric runtime
  README_VSCode.md                  # detailed run guide (Fabric vs local)
  requirements-local.txt            # Python deps for local kernel mode
  Revenue_Forecasting_Manual.pdf    # forecasting manual
Revenue_Forecast_Notebook.Notebook/
  notebook-content.py               # Fabric Git-synced notebook source
  .platform                         # Fabric notebook metadata
```

## Tech stack

- **Platform:** Microsoft Fabric (Spark / lakehouse) and OneLake (Delta)
- **Languages / runtimes:** Python, PySpark (Fabric), Jupyter
- **Modeling:** Prophet, statsmodels (Holt), scikit-learn, LightGBM; optional XGBoost and CatBoost
- **Data access:** Spark tables in Fabric; `deltalake` + `azure-identity` locally
- **Tracking:** MLflow (`sales_revenue_forecast` experiment; `synapseml-mlflow` for Fabric from a laptop)

## Prerequisites

- Access to the Fabric workspace and Gold lakehouse (Member/Contributor for writes)
- For local runs: Python 3, Azure CLI (`az login`) or another Entra ID credential that `DefaultAzureCredential` can use
- Network reachability to `onelake.dfs.fabric.microsoft.com`

Workspace and lakehouse IDs used by local mode live in the notebook parameters cell (`WORKSPACE_ID`, `GOLD_LAKEHOUSE_ID`). Update those if you point at a different lakehouse.

## Run on Fabric Spark (recommended for production-identical behaviour)

1. Open the notebook in the Fabric portal, or use VS Code with the **Fabric Data Engineering** extension and the **Microsoft Fabric Runtime** kernel.
2. Run all cells. `spark` is present → `RUN_MODE = "fabric-spark"`.
3. The first cell installs Prophet / XGBoost / CatBoost in the remote session when missing.

Tables are read and written with Spark; MLflow logs to the workspace experiment.

## Run locally in VS Code

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r files/requirements-local.txt
az login
```

Open `files/Revenue_Forecast_Notebook.ipynb`, select the `.venv` kernel, and run the cells in order. Without `spark`, the notebook sets `RUN_MODE = "local"`, reads/writes Gold Delta tables over OneLake, and logs MLflow to Fabric when `synapseml-mlflow` works (otherwise `./mlruns`).

For full comparison of Fabric vs local mode, authentication notes, and unattended service-principal setup, see [files/README_VSCode.md](files/README_VSCode.md).

## Configuration

Key parameters are in the notebook config cell, including:

- Input / output table names and `KEEP_FORECAST_HISTORY`
- `HORIZON_MONTHS`, `BACKTEST_FOLDS`, `BACKTEST_HORIZON_MONTHS`, `INTERVAL_WIDTH`
- `INCLUDE_TAX`, MLflow experiment / registered model names
- `LOCAL_MLFLOW_TO_FABRIC` for laptop → Fabric tracking

Do not commit credentials, local MLflow runs, Fabric caches, or exported lakehouse data. `.gitignore` already excludes common machine-specific paths (`.venv/`, `mlruns/`, `.env`, Fabric VS Code caches, and similar).

## Documentation

- [VS Code / local run guide](files/README_VSCode.md)
- [Forecasting manual (PDF)](files/Revenue_Forecasting_Manual.pdf)

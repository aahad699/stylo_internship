"""OneLake helpers for the SolarRAG lakehouse.

Used as storage medium:
  Files/solar_rag/docs/          — source PDFs
  Files/solar_rag/vectorstore/   — FAISS index files
  Tables/dbo/solar_rag_chunks    — chunk text + embedding vectors (Delta)

Works in two modes (same idea as sales_predictor):
  fabric-spark — spark session exists (VS Code Fabric Runtime / portal)
  local        — deltalake + azure-identity over abfss://
"""

from __future__ import annotations

import json
from pathlib import Path

import config as cfg


def lakehouse_configured() -> bool:
    return bool(cfg.FABRIC_LAKEHOUSE_ID) and "REPLACE" not in cfg.FABRIC_LAKEHOUSE_ID


def onelake_root() -> str:
    return (
        f"abfss://{cfg.FABRIC_WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/"
        f"{cfg.FABRIC_LAKEHOUSE_ID}"
    )


def onelake_table_path(table: str = cfg.FABRIC_CHUNKS_TABLE) -> str:
    return f"{onelake_root()}/Tables/{cfg.FABRIC_SCHEMA}/{table}"


def onelake_files_path(prefix: str) -> str:
    return f"{onelake_root()}/{prefix.strip('/')}"


def detect_run_mode() -> str:
    """Prefer an active Spark session (Fabric Runtime); otherwise local OneLake SDK."""
    import sys

    main = sys.modules.get("__main__")
    if main is not None and getattr(main, "spark", None) is not None:
        return "fabric-spark"
    try:
        from pyspark.sql import SparkSession

        if SparkSession.getActiveSession() is not None:
            return "fabric-spark"
    except Exception:
        pass
    return "local"


def _active_spark():
    import sys

    main = sys.modules.get("__main__")
    if main is not None and getattr(main, "spark", None) is not None:
        return main.spark
    from pyspark.sql import SparkSession

    session = SparkSession.getActiveSession()
    if session is None:
        raise RuntimeError("No active Spark session.")
    return session


def local_storage_options() -> dict:
    from azure.identity import DefaultAzureCredential

    token = DefaultAzureCredential().get_token("https://storage.azure.com/.default").token
    return {"bearer_token": token, "use_fabric_endpoint": "true"}


def upload_directory_to_files(local_dir: Path, files_prefix: str) -> None:
    """Copy every file under local_dir into OneLake Files/<files_prefix>/."""
    if not lakehouse_configured():
        raise RuntimeError(
            "FABRIC_LAKEHOUSE_ID is not set. Create the SolarRAG lakehouse and paste its id in config.py."
        )

    local_dir = Path(local_dir)
    files = [p for p in local_dir.rglob("*") if p.is_file()]
    if not files:
        raise FileNotFoundError(f"Nothing to upload under {local_dir}")

    mode = detect_run_mode()
    remote_root = onelake_files_path(files_prefix)

    if mode == "fabric-spark":
        # mssparkutils is available on Fabric Runtime
        from notebookutils import mssparkutils  # type: ignore

        for path in files:
            rel = path.relative_to(local_dir).as_posix()
            dest = f"{remote_root}/{rel}"
            mssparkutils.fs.put(dest, path.read_bytes(), True)
        return

    # Local: Azure Data Lake FileSystemClient over the Fabric DFS endpoint
    from azure.identity import DefaultAzureCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    account_url = f"https://onelake.dfs.fabric.microsoft.com"
    service = DataLakeServiceClient(account_url, credential=DefaultAzureCredential())
    fs = service.get_file_system_client(cfg.FABRIC_WORKSPACE_ID)

    for path in files:
        rel = path.relative_to(local_dir).as_posix()
        remote = f"{cfg.FABRIC_LAKEHOUSE_ID}/{files_prefix.strip('/')}/{rel}"
        file_client = fs.get_file_client(remote)
        with path.open("rb") as handle:
            file_client.upload_data(handle, overwrite=True)


def write_chunks_delta(rows: list[dict]) -> None:
    """Persist chunk rows (text, embedding, metadata) as a Delta table in SolarRAG."""
    if not lakehouse_configured():
        raise RuntimeError(
            "FABRIC_LAKEHOUSE_ID is not set. Create the SolarRAG lakehouse and paste its id in config.py."
        )
    if not rows:
        raise ValueError("No chunk rows to write.")

    import pandas as pd

    frame = pd.DataFrame(rows)
    # Store embedding as JSON string so Delta schema stays simple across Spark / delta-rs
    if "embedding" in frame.columns:
        frame["embedding"] = frame["embedding"].apply(
            lambda v: json.dumps(v) if not isinstance(v, str) else v
        )

    mode = detect_run_mode()
    table = cfg.FABRIC_CHUNKS_TABLE

    if mode == "fabric-spark":
        spark = _active_spark()
        spark_df = spark.createDataFrame(frame)
        (
            spark_df.write.format("delta")
            .mode("overwrite")
            .option("overwriteSchema", "true")
            .saveAsTable(f"{cfg.FABRIC_LAKEHOUSE_NAME}.{cfg.FABRIC_SCHEMA}.{table}")
        )
        return

    from deltalake import write_deltalake

    write_deltalake(
        onelake_table_path(table),
        frame,
        mode="overwrite",
        schema_mode="overwrite",
        storage_options=local_storage_options(),
    )


def sync_local_artifacts(
    *,
    docs_dir: Path = cfg.DATA_DIR,
    vectorstore_dir: Path = cfg.VECTORSTORE_DIR,
    chunk_rows: list[dict] | None = None,
) -> dict:
    """Push docs + FAISS files + optional chunk table to the SolarRAG lakehouse."""
    summary = {"docs": False, "vectorstore": False, "chunks_table": False, "mode": detect_run_mode()}

    upload_directory_to_files(docs_dir, cfg.FABRIC_DOCS_FILES_PREFIX)
    summary["docs"] = True

    upload_directory_to_files(vectorstore_dir, cfg.FABRIC_VECTORSTORE_FILES_PREFIX)
    summary["vectorstore"] = True

    if chunk_rows is not None:
        write_chunks_delta(chunk_rows)
        summary["chunks_table"] = True

    return summary

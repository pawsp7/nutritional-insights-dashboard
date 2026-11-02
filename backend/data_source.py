"""Reads All_Diets.csv from Azure Blob Storage and keeps it warm between calls.

Configuration comes from app settings (environment variables), never from code:
  DIETS_STORAGE_CONNECTION  connection string of the storage account holding the dataset
                            (falls back to AzureWebJobsStorage, the Function App's own account)
  DIETS_CONTAINER           blob container, default "datasets"
  DIETS_BLOB                blob name, default "All_Diets.csv"
  DIETS_CACHE_SECONDS       how long a warm instance reuses the parsed data, default 300
"""
import io
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
from azure.storage.blob import BlobServiceClient

import analysis


@dataclass
class Dataset:
	df: pd.DataFrame
	account: str
	container: str
	blob: str
	etag: str | None
	last_modified: datetime | None
	size_bytes: int
	loaded_at: float
	load_ms: float
	raw_rows: int


_lock = threading.Lock()
_cached: Dataset | None = None


def settings() -> dict:
	return {"connection": os.environ.get("DIETS_STORAGE_CONNECTION") or os.environ.get("AzureWebJobsStorage", ""), "container": os.environ.get("DIETS_CONTAINER", "datasets"), "blob": os.environ.get("DIETS_BLOB", "All_Diets.csv"), "cache_seconds": int(os.environ.get("DIETS_CACHE_SECONDS", "300")), }


def _account_name(connection: str) -> str:
	if connection.strip().lower() == "usedevelopmentstorage=true":
		return "devstoreaccount1 (Azurite)"
	parts = dict(p.split("=", 1) for p in connection.split(";") if "=" in p)
	return parts.get("AccountName", "unknown")


def _blob_client(cfg: dict):
	if not cfg["connection"]:
		raise RuntimeError("no storage connection configured (set DIETS_STORAGE_CONNECTION)")
	service = BlobServiceClient.from_connection_string(cfg["connection"])
	return service.get_blob_client(cfg["container"], cfg["blob"])


def blob_properties(cfg: dict):
	return _blob_client(cfg).get_blob_properties()


def _download(cfg: dict) -> Dataset:
	started = time.perf_counter()
	stream = _blob_client(cfg).download_blob()
	raw = pd.read_csv(io.BytesIO(stream.readall()))
	props = stream.properties
	df = analysis.clean(raw)
	return Dataset(df=df, account=_account_name(cfg["connection"]), container=cfg["container"], blob=cfg["blob"], etag=props.etag, last_modified=props.last_modified, size_bytes=props.size, loaded_at=time.time(), load_ms=round((time.perf_counter() - started) * 1000, 1), raw_rows=len(raw), )


def load(refresh: bool = False) -> tuple[Dataset, str]:
	"""Return the dataset and whether it came from the warm cache ("hit") or Blob Storage ("miss")."""
	global _cached
	cfg = settings()
	with _lock:
		fresh = _cached is not None and time.time() - _cached.loaded_at < cfg["cache_seconds"]
		if fresh and not refresh:
			return _cached, "hit"
		_cached = _download(cfg)
		return _cached, "miss"


def describe(ds: Dataset) -> dict:
	return {"storageAccount": ds.account, "container": ds.container, "blob": ds.blob, "etag": ds.etag, "blobLastModified": ds.last_modified.isoformat() if ds.last_modified else None, "sizeBytes": ds.size_bytes, "rawRows": ds.raw_rows, "cleanRows": int(len(ds.df)), "loadedAt": datetime.fromtimestamp(ds.loaded_at, timezone.utc).isoformat(), "blobLoadMs": ds.load_ms, }

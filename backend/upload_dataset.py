"""Upload data/All_Diets.csv to the dataset container (Azurite locally, or a real account).

    .venv/Scripts/python upload_dataset.py                      # uses DIETS_STORAGE_CONNECTION from local.settings.json
    .venv/Scripts/python upload_dataset.py --connection "<conn>" # any storage account

The cloud deployment does the same thing with `az storage blob upload` (infra/deploy.ps1).
"""
import argparse
import os
from pathlib import Path

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings

from dev_server import load_settings

ROOT = Path(__file__).resolve().parents[1]


def main():
    load_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("DIETS_STORAGE_CONNECTION"))
    parser.add_argument("--container", default=os.environ.get("DIETS_CONTAINER", "datasets"))
    parser.add_argument("--blob", default=os.environ.get("DIETS_BLOB", "All_Diets.csv"))
    parser.add_argument("--file", default=str(ROOT / "data" / "All_Diets.csv"))
    args = parser.parse_args()

    service = BlobServiceClient.from_connection_string(args.connection)
    try:
        service.create_container(args.container)  # private: no public access level
        print(f"created container {args.container}")
    except ResourceExistsError:
        pass
    with open(args.file, "rb") as fh:
        service.get_blob_client(args.container, args.blob).upload_blob(
            fh, overwrite=True, content_settings=ContentSettings(content_type="text/csv")
        )
    print(f"uploaded {args.file} -> {service.account_name}/{args.container}/{args.blob}")


if __name__ == "__main__":
    main()

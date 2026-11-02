import os
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import analysis  # noqa: E402

CSV = BACKEND.parent / "data" / "All_Diets.csv"


@pytest.fixture(scope="session")
def real_df():
	return analysis.clean(pd.read_csv(CSV))


@pytest.fixture
def small_raw():
	return pd.DataFrame({"Diet_type": ["Vegan", "vegan ", "keto", "keto", "paleo", "paleo"], "Recipe_name": ["Tofu Bowl", "Lentil Soup", "Steak", "Egg Cups", "Pho", "Collard Burrito"], "Cuisine_type": ["asian", "Indian", "american", "american", "south east asian", None], "Protein(g)": [20.0, None, 80.0, 30.0, 60.0, 44.6], "Carbs(g)": [50.0, 70.0, 0.0, 4.0, 270.0, 28.7], "Fat(g)": [10.0, 5.0, 60.0, 0.0, 400.0, 78.5], "Extraction_day": ["10/16/2022"] * 6, "Extraction_time": ["17:20:09"] * 6, })


@pytest.fixture
def small_df(small_raw):
	return analysis.clean(small_raw)


@pytest.fixture(autouse=True)
def no_real_storage(monkeypatch):
	for key in ("DIETS_STORAGE_CONNECTION", "AzureWebJobsStorage"):
		monkeypatch.delenv(key, raising=False)
	os.environ.setdefault("DIETS_CACHE_SECONDS", "300")

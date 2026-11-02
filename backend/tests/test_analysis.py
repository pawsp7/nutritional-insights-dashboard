import json
import math

import pytest

import analysis


def test_clean_fills_missing_macros_with_mean(small_df, small_raw):
	expected = small_raw["Protein(g)"].mean()
	assert small_df.loc[1, "Protein(g)"] == pytest.approx(expected)
	assert small_df[analysis.MACROS].isna().sum().sum() == 0


def test_clean_normalises_text(small_df):
	assert sorted(small_df["Diet_type"].unique()) == ["keto", "paleo", "vegan"]
	assert small_df.loc[1, "Cuisine_type"] == "indian"
	assert small_df.loc[5, "Cuisine_type"] == "unknown"


def test_ratios_leave_division_by_zero_blank(small_df):
	steak = small_df[small_df["Recipe_name"] == "Steak"].iloc[0]
	eggs = small_df[small_df["Recipe_name"] == "Egg Cups"].iloc[0]
	assert math.isnan(steak["Protein_to_Carbs_ratio"])
	assert math.isnan(eggs["Carbs_to_Fat_ratio"])
	assert eggs["Protein_to_Carbs_ratio"] == pytest.approx(7.5)


def test_clean_rejects_wrong_schema(small_raw):
	with pytest.raises(ValueError):
		analysis.clean(small_raw.drop(columns=["Fat(g)"]))


def test_average_macros_matches_phase1(real_df):
	avg = analysis.average_macros(real_df)
	assert avg["labels"] == ["dash", "keto", "mediterranean", "paleo", "vegan"]
	expected = real_df.groupby("Diet_type")["Protein(g)"].mean()
	for diet, value in zip(avg["labels"], avg["series"]["protein"]):
		assert value == pytest.approx(expected[diet], abs=0.01)


def test_insights_payload_is_json_safe(real_df):
	data = analysis.insights(real_df)
	json.dumps(data, allow_nan=False)  # NaN/inf would raise here
	assert data["summary"]["recipes"] == 7806
	assert data["summary"]["highestProteinDiet"]["diet"] == real_df.groupby("Diet_type")["Protein(g)"].mean().idxmax()
	assert data["distribution"]["by"] == "diet"
	assert sum(data["distribution"]["values"]) == 7806
	assert len(data["correlations"]["matrix"]) == 3
	assert data["correlations"]["matrix"][0][0] == 1.0
	assert len(data["topProtein"]) == 25


def test_insights_for_one_diet_switches_to_cuisines(real_df):
	data = analysis.insights(real_df, "keto")
	assert data["summary"]["recipes"] == 1512
	assert data["distribution"]["by"] == "cuisine"
	assert len(data["distribution"]["labels"]) <= 9
	assert sum(data["distribution"]["values"]) == 1512
	assert {p["diet"] for p in data["scatter"]} == {"keto"}


def test_scatter_sample_covers_every_diet(real_df):
	points = analysis.scatter_sample(real_df, limit=100)
	assert len(points) == 100
	assert {p["diet"] for p in points} == set(analysis.diet_types(real_df))


def test_unknown_diet_raises(real_df):
	with pytest.raises(LookupError):
		analysis.insights(real_df, "carnivore")


def test_recipes_pagination_and_sort(real_df):
	page1 = analysis.recipes(real_df, page=1, page_size=10)
	assert page1["total"] == 7806 and page1["totalPages"] == 781
	proteins = [r["protein"] for r in page1["items"]]
	assert proteins == sorted(proteins, reverse=True)
	last = analysis.recipes(real_df, page=9999, page_size=10)
	assert last["page"] == 781 and len(last["items"]) == 6


def test_recipes_search_matches_name_diet_or_cuisine(real_df):
	by_name = analysis.recipes(real_df, search="guacamole", page_size=100)
	assert by_name["total"] > 0
	assert all("guacamole" in r["name"].lower() for r in by_name["items"])
	by_cuisine = analysis.recipes(real_df, diet="vegan", search="japanese", page_size=100)
	assert all(r["diet"] == "vegan" for r in by_cuisine["items"])
	assert by_cuisine["total"] > 0


def test_kmeans_separates_obvious_groups():
	import numpy as np
	pts = np.array([[0, 0], [0.1, 0], [0, 0.1], [10, 10], [10.1, 10], [10, 10.1]], dtype=float)
	labels, _ = analysis.kmeans(pts, 2)
	assert len(set(labels[:3])) == 1 and len(set(labels[3:])) == 1 and labels[0] != labels[3]


def test_clusters_are_repeatable_and_complete(real_df):
	first = analysis.clusters(real_df, k=4)
	second = analysis.clusters(real_df, k=4)
	assert first == second
	assert sum(c["size"] for c in first["clusters"]) == 7806
	sizes = [c["size"] for c in first["clusters"]]
	assert sizes == sorted(sizes, reverse=True)
	assert {p["cluster"] for p in first["points"]} <= {0, 1, 2, 3}
	json.dumps(first, allow_nan=False)

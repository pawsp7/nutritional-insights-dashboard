"""Pure pandas/numpy analysis of the Diets dataset.

Nothing here talks to Azure, so every function can be unit tested with a plain
DataFrame. function_app.py loads the blob and hands the DataFrame in.
"""
import numpy as np
import pandas as pd

MACROS = ["Protein(g)", "Carbs(g)", "Fat(g)"]
MACRO_KEYS = {"Protein(g)": "protein", "Carbs(g)": "carbs", "Fat(g)": "fat"}
TEXT_COLUMNS = ["Diet_type", "Recipe_name", "Cuisine_type"]
SORT_COLUMNS = {"protein": "Protein(g)", "carbs": "Carbs(g)", "fat": "Fat(g)", "name": "Recipe_name"}


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    """Same cleaning rules as Phase 1, plus normalising the text columns."""
    missing = [c for c in TEXT_COLUMNS + MACROS if c not in raw.columns]
    if missing:
        raise ValueError(f"dataset is missing columns: {', '.join(missing)}")
    df = raw.copy()
    for col in MACROS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Phase 1 rule: null macros become the column mean
    df[MACROS] = df[MACROS].fillna(df[MACROS].mean())
    for col in TEXT_COLUMNS:
        df[col] = df[col].astype("string").str.strip()
    df["Diet_type"] = df["Diet_type"].str.lower()
    df["Cuisine_type"] = df["Cuisine_type"].str.lower().fillna("unknown")
    df = df.dropna(subset=["Diet_type", "Recipe_name"])
    df["Protein_to_Carbs_ratio"] = df["Protein(g)"] / df["Carbs(g)"].where(df["Carbs(g)"] != 0)
    # 0 g fat would make the ratio infinite, so it is left blank instead
    df["Carbs_to_Fat_ratio"] = df["Carbs(g)"] / df["Fat(g)"].where(df["Fat(g)"] != 0)
    return df.reset_index(drop=True)


def diet_types(df: pd.DataFrame) -> list[str]:
    return sorted(df["Diet_type"].dropna().unique().tolist())


def filter_diet(df: pd.DataFrame, diet: str | None) -> pd.DataFrame:
    if not diet or diet.lower() == "all":
        return df
    return df[df["Diet_type"] == diet.lower()]


def _num(value, digits=2):
    """JSON-safe rounding: NaN/inf become None."""
    if value is None or pd.isna(value) or np.isinf(value):
        return None
    return round(float(value), digits)


def average_macros(df: pd.DataFrame) -> dict:
    avg = df.groupby("Diet_type")[MACROS].mean().sort_index()
    return {
        "labels": avg.index.tolist(),
        "series": {MACRO_KEYS[m]: [_num(v) for v in avg[m]] for m in MACROS},
    }


def distribution(df: pd.DataFrame, diet: str | None, top: int = 8) -> dict:
    """Recipe counts by diet, or by cuisine once a single diet is selected."""
    if diet and diet.lower() != "all":
        counts = df["Cuisine_type"].value_counts()
        by = "cuisine"
        if len(counts) > top:
            counts = pd.concat([counts.iloc[:top], pd.Series({"other": counts.iloc[top:].sum()})])
    else:
        counts = df["Diet_type"].value_counts().sort_index()
        by = "diet"
    return {"by": by, "labels": [str(i) for i in counts.index], "values": [int(v) for v in counts.values]}


def correlations(df: pd.DataFrame) -> dict:
    corr = df[MACROS].corr()
    labels = [MACRO_KEYS[m] for m in MACROS]
    return {"labels": labels, "matrix": [[_num(corr.loc[r, c], 3) for c in MACROS] for r in MACROS]}


def scatter_sample(df: pd.DataFrame, limit: int = 600, seed: int = 42) -> list[dict]:
    """Protein vs carbs points, sampled evenly per diet so small diets still show up."""
    if len(df) > limit:
        per_diet = max(1, limit // max(1, df["Diet_type"].nunique()))
        df = df.sample(frac=1, random_state=seed).groupby("Diet_type").head(per_diet)
    return [
        {"diet": r.Diet_type, "name": r.Recipe_name, "protein": _num(r.protein), "carbs": _num(r.carbs), "fat": _num(r.fat)}
        for r in df.rename(columns={"Protein(g)": "protein", "Carbs(g)": "carbs", "Fat(g)": "fat"}).itertuples()
    ]


def top_protein(df: pd.DataFrame, n: int = 5) -> list[dict]:
    top = df.sort_values("Protein(g)", ascending=False).groupby("Diet_type", group_keys=False).head(n)
    return [_recipe(r) for _, r in top.iterrows()]


def common_cuisines(df: pd.DataFrame) -> list[dict]:
    rows = []
    for diet, group in df.groupby("Diet_type"):
        counts = group["Cuisine_type"].value_counts()
        rows.append({"diet": diet, "cuisine": counts.index[0], "count": int(counts.iloc[0])})
    return rows


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"recipes": 0}
    avg = df.groupby("Diet_type")["Protein(g)"].mean()
    best = df.loc[df["Protein(g)"].idxmax()]
    return {
        "recipes": int(len(df)),
        "dietTypes": int(df["Diet_type"].nunique()),
        "cuisines": int(df["Cuisine_type"].nunique()),
        "avgProtein": _num(df["Protein(g)"].mean()),
        "avgCarbs": _num(df["Carbs(g)"].mean()),
        "avgFat": _num(df["Fat(g)"].mean()),
        "highestProteinDiet": {"diet": avg.idxmax(), "avgProtein": _num(avg.max())},
        "highestProteinRecipe": _recipe(best),
        "medianProteinToCarbs": _num(df["Protein_to_Carbs_ratio"].median(), 3),
        "medianCarbsToFat": _num(df["Carbs_to_Fat_ratio"].median(), 3),
    }


def insights(df: pd.DataFrame, diet: str | None = None) -> dict:
    subset = filter_diet(df, diet)
    if subset.empty:
        raise LookupError(f"no recipes for diet type '{diet}'")
    return {
        "summary": summary(subset),
        "averageMacros": average_macros(subset),
        "distribution": distribution(subset, diet),
        "correlations": correlations(subset),
        "scatter": scatter_sample(subset),
        "topProtein": top_protein(subset),
        "commonCuisines": common_cuisines(subset),
    }


def _recipe(row) -> dict:
    return {
        "name": row["Recipe_name"],
        "diet": row["Diet_type"],
        "cuisine": row["Cuisine_type"],
        "protein": _num(row["Protein(g)"]),
        "carbs": _num(row["Carbs(g)"]),
        "fat": _num(row["Fat(g)"]),
        "proteinToCarbs": _num(row["Protein_to_Carbs_ratio"], 3),
        "carbsToFat": _num(row["Carbs_to_Fat_ratio"], 3),
    }


def recipes(df, diet=None, search=None, sort="protein", order="desc", page=1, page_size=10) -> dict:
    subset = filter_diet(df, diet)
    if search:
        term = search.strip().lower()
        hit = pd.Series(False, index=subset.index)
        for col in TEXT_COLUMNS:
            hit = hit | subset[col].str.lower().str.contains(term, regex=False, na=False)
        subset = subset[hit]
    column = SORT_COLUMNS[sort]
    subset = subset.sort_values(column, ascending=(order == "asc"), kind="stable")
    total = len(subset)
    pages = max(1, -(-total // page_size))
    page = min(page, pages)
    start = (page - 1) * page_size
    return {
        "items": [_recipe(r) for _, r in subset.iloc[start:start + page_size].iterrows()],
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": pages,
    }


def kmeans(points: np.ndarray, k: int, seed: int = 42, iterations: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """Small k-means with k-means++ seeding; fixed seed so results are repeatable."""
    rng = np.random.default_rng(seed)
    centers = [points[rng.integers(len(points))]]
    for _ in range(1, k):
        dist = np.min([((points - c) ** 2).sum(axis=1) for c in centers], axis=0)
        p = dist / dist.sum() if dist.sum() > 0 else None  # identical points: fall back to uniform
        centers.append(points[rng.choice(len(points), p=p)])
    centers = np.array(centers)
    labels = np.zeros(len(points), dtype=int)
    for _ in range(iterations):
        labels = ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
        moved = np.array([points[labels == i].mean(axis=0) if (labels == i).any() else centers[i] for i in range(k)])
        if np.allclose(moved, centers):
            break
        centers = moved
    return labels, centers


def _profile_name(share: dict, size_ratio: float) -> str:
    """Name a cluster after the macro it leans on most (relative to the dataset) and its portion size."""
    lead = max(share, key=share.get)
    name = {"protein": "high-protein", "carbs": "high-carb", "fat": "high-fat"}[lead] if share[lead] > 1.1 else "balanced"
    if size_ratio < 0.5:
        name = f"light {name}"
    elif size_ratio > 1.75:
        name = f"large {name}"
    return name[0].upper() + name[1:]


def clusters(df: pd.DataFrame, k: int = 3, diet: str | None = None, sample: int = 600) -> dict:
    """Group recipes by macro profile (share of protein/carbs/fat grams plus overall size)."""
    subset = filter_diet(df, diet)
    if len(subset) < k:
        raise LookupError(f"need at least {k} recipes to build {k} clusters")
    grams = subset[MACROS].to_numpy(dtype=float)
    total = grams.sum(axis=1, keepdims=True)
    shares = np.divide(grams, total, out=np.full_like(grams, 1 / 3), where=total > 0)
    size = np.log1p(total)
    features = np.hstack([shares, size])
    features = (features - features.mean(axis=0)) / np.where(features.std(axis=0) == 0, 1, features.std(axis=0))
    labels, _ = kmeans(features, k)
    overall_share = shares.mean(axis=0)
    groups = []
    for i in range(k):
        members = subset[labels == i]
        rel = shares[labels == i].mean(axis=0) / overall_share
        size_ratio = total[labels == i].mean() / total.mean()
        groups.append({
            "id": i,
            "label": _profile_name(dict(zip(["protein", "carbs", "fat"], rel)), size_ratio),
            "size": int(len(members)),
            "centroid": {MACRO_KEYS[m]: _num(members[m].mean()) for m in MACROS},
            "topDiet": members["Diet_type"].value_counts().index[0],
            "dietMix": {d: int(c) for d, c in members["Diet_type"].value_counts().items()},
        })
    # largest cluster first, and renumber so colours stay stable between calls
    order = sorted(range(k), key=lambda i: -groups[i]["size"])
    remap = {old: new for new, old in enumerate(order)}
    groups = [dict(groups[old], id=remap[old]) for old in order]
    labels = np.vectorize(remap.get)(labels)
    rng = np.random.default_rng(7)
    picks = rng.choice(len(subset), size=min(sample, len(subset)), replace=False)
    points = [
        {"cluster": int(labels[i]), "protein": _num(grams[i, 0]), "carbs": _num(grams[i, 1]), "fat": _num(grams[i, 2]),
         "name": subset.iloc[i]["Recipe_name"]}
        for i in sorted(picks)
    ]
    return {"k": k, "features": ["protein share", "carbs share", "fat share", "log total grams"], "clusters": groups, "points": points}

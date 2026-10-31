"""HTTP API for the Nutritional Insights dashboard (Azure Functions, Python v2 model).

GET /api/health     settings check and blob reachability
GET /api/insights   chart data: averages, distribution, correlations, scatter sample, summary
GET /api/recipes    paginated, searchable recipe list
GET /api/clusters   k-means groups of recipes by macro profile

Every response is {"data": ..., "meta": {...}} where meta carries the execution time,
cache status and which blob the numbers came from.
"""
import json
import logging
import os
import platform
import time
from datetime import datetime, timezone

import azure.functions as func

import analysis
import data_source

# Anonymous: the dashboard is public and a function key in browser JS would not be a secret anyway.
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

_cold = True


class BadRequest(ValueError):
    pass


def _int_param(req, name, default, low, high):
    raw = req.params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be a whole number")
    if not low <= value <= high:
        raise BadRequest(f"'{name}' must be between {low} and {high}")
    return value


def _choice_param(req, name, default, choices):
    value = (req.params.get(name) or default).lower()
    if value not in choices:
        raise BadRequest(f"'{name}' must be one of: {', '.join(choices)}")
    return value


def _diet_param(req, df):
    diet = (req.params.get("diet") or "all").strip().lower()
    if diet != "all" and diet not in analysis.diet_types(df):
        raise LookupError(f"unknown diet type '{diet}'")
    return diet


def _json(body, status=200):
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/json")


def _instance():
    return {
        "app": os.environ.get("WEBSITE_SITE_NAME", "local"),
        "region": os.environ.get("REGION_NAME", "local"),
        "python": platform.python_version(),
    }


def _handle(endpoint, req, build):
    """Shared plumbing: load data, time the call, wrap the result, map errors to HTTP codes."""
    global _cold
    started = time.perf_counter()
    cold, _cold = _cold, False
    try:
        refresh = req.params.get("refresh", "").lower() in ("1", "true", "yes")
        ds, cache = data_source.load(refresh=refresh)
        data, filters = build(ds.df)
        status = 200
        body = {"data": data}
        meta_extra = {"cache": cache, "dataset": data_source.describe(ds), "filters": filters}
    except BadRequest as exc:
        status, body, meta_extra = 400, {"error": str(exc)}, {}
    except LookupError as exc:
        status, body, meta_extra = 404, {"error": str(exc)}, {}
    except Exception as exc:  # storage or parsing failure: report it instead of a bare 500
        logging.exception("%s failed", endpoint)
        status, body, meta_extra = 503, {"error": f"data source unavailable: {exc}"}, {}
    body["meta"] = {
        "endpoint": endpoint,
        "executionTimeMs": round((time.perf_counter() - started) * 1000, 1),
        "coldStart": cold,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "instance": _instance(),
        **meta_extra,
    }
    logging.info("%s -> %s in %sms (cache %s)", endpoint, status, body["meta"]["executionTimeMs"], meta_extra.get("cache"))
    return _json(body, status)


@app.route(route="insights", methods=["GET"])
def insights(req: func.HttpRequest) -> func.HttpResponse:
    def build(df):
        diet = _diet_param(req, df)
        data = analysis.insights(df, diet)
        data["dietTypes"] = analysis.diet_types(df)
        return data, {"diet": diet}

    return _handle("insights", req, build)


@app.route(route="recipes", methods=["GET"])
def recipes(req: func.HttpRequest) -> func.HttpResponse:
    def build(df):
        diet = _diet_param(req, df)
        filters = {
            "diet": diet,
            "search": (req.params.get("search") or "").strip()[:100],
            "sort": _choice_param(req, "sort", "protein", list(analysis.SORT_COLUMNS)),
            "order": _choice_param(req, "order", "desc", ["asc", "desc"]),
            "page": _int_param(req, "page", 1, 1, 100000),
            "pageSize": _int_param(req, "pageSize", 10, 1, 100),
        }
        data = analysis.recipes(df, diet, filters["search"], filters["sort"], filters["order"], filters["page"], filters["pageSize"])
        return data, filters

    return _handle("recipes", req, build)


@app.route(route="clusters", methods=["GET"])
def clusters(req: func.HttpRequest) -> func.HttpResponse:
    def build(df):
        diet = _diet_param(req, df)
        k = _int_param(req, "k", 3, 2, 6)
        return analysis.clusters(df, k, diet), {"diet": diet, "k": k}

    return _handle("clusters", req, build)


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    cfg = data_source.settings()
    body = {
        "status": "ok",
        "storageConfigured": bool(cfg["connection"]),
        "container": cfg["container"],
        "blob": cfg["blob"],
        "cacheSeconds": cfg["cache_seconds"],
        "instance": _instance(),
        "time": datetime.now(timezone.utc).isoformat(),
    }
    status = 200
    try:
        props = data_source.blob_properties(cfg)
        body["blobReachable"] = True
        body["blobLastModified"] = props.last_modified.isoformat()
        body["blobSizeBytes"] = props.size
    except Exception as exc:
        body.update(status="degraded", blobReachable=False, error=str(exc))
        status = 503
    return _json(body, status)

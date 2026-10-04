"""Complaint categories within a department (data/grievance/categories.yaml)."""
import os

import yaml

from .adapters import LATIN, normalize

_PATH = os.path.join(os.path.dirname(__file__), os.pardir, "data", "grievance", "categories.yaml")
_CACHE = {}


def categories():
    if "c" not in _CACHE:
        with open(_PATH, encoding="utf-8") as f:
            _CACHE["c"] = (yaml.safe_load(f) or {}).get("categories", {})
    return _CACHE["c"]


def _hit(kw, norm):
    k = str(kw).lower().strip()
    return f" {k} " in norm if LATIN.search(k) else k in norm


def categorize(department, text):
    cats = categories().get(department or "", [])
    if not cats:
        return "other"
    norm = normalize(text or "")
    for c in cats:
        if any(_hit(k, norm) for k in c.get("keywords", [])):
            return c["key"]
    return cats[0]["key"]


def category_name(department, key):
    for c in categories().get(department or "", []):
        if c["key"] == key:
            return c["name"]
    return "Other" if key == "other" else str(key or "")


def category_options(department):
    return [(c["key"], c["name"]) for c in categories().get(department or "", [])] or [("other", "Other")]

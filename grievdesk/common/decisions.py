"""Append-only store for human decisions (PII masked), kept next to the AI audit log."""
import json
import os
import time

from .pii import mask


class DecisionStore:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def save(self, record: dict):
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               **{k: mask(v) if isinstance(v, str) else v for k, v in record.items()}}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def all(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def by_id(self):
        out = {}
        for d in self.all():
            out.setdefault(d["id"], []).append(d)
        return out

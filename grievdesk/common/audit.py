"""Append-only JSONL audit log. Every AI suggestion is logged with PII masked."""
import json
import os
import time
import uuid

from .pii import mask_obj


class AuditLog:
    def __init__(self, path: str, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        if enabled:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def record(self, system: str, case_id, output: dict, pii_terms=()):
        entry = {
            "event_id": uuid.uuid4().hex[:12],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "system": system,
            "case_id": case_id,
            "ai_output": mask_obj(output, pii_terms),
            "human_decision": None,  # filled by the reviewer in a real deployment
        }
        if self.enabled:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read_all(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

"""Multilingual citizen grievance router (MVP).

Pipeline: understand -> classify -> safety screen -> confidence check -> respond (templates only)
-> draft reply for officer approval. The bot never makes a final decision.
"""
import difflib
import hashlib
import re

import yaml

from .common.audit import AuditLog
from .common.pii import mask, find_pii
from .adapters import OfflineLanguageID, normalize, LATIN


class FaultInjected(RuntimeError):
    pass


class GrievanceRouter:
    name = "Multilingual Citizen Grievance Chatbot"

    def __init__(self, cfg: dict, audit: AuditLog | None = None):
        self.cfg = cfg
        with open(cfg["routing_matrix"], encoding="utf-8") as f:
            self.matrix = yaml.safe_load(f)
        with open(cfg["templates"], encoding="utf-8") as f:
            self.tpl = yaml.safe_load(f)
        self.depts = self.matrix["departments"]
        self.th = cfg.get("thresholds", {})
        self.lang_support = cfg.get("language_support", {})
        self.langid = OfflineLanguageID(self.matrix.get("language_markers", {}))
        self.injection = [re.compile(p, re.I) for p in self.matrix.get("injection_patterns", [])]
        self.data_req = [re.compile(p, re.I) for p in self.matrix.get("data_request_patterns", [])]
        self.audit = audit or AuditLog("/tmp/aigp_grievance_audit.jsonl", enabled=False)
        self.latin_vocab = sorted({k for d in self.depts.values() for k in d["keywords"]
                                   if LATIN.search(k) and " " not in k})

    # ----------------------------------------------------------------- helpers
    def _hit(self, kw: str, norm: str, tokens: set) -> bool:
        kw_l = kw.lower()
        if not LATIN.search(kw_l):                      # Devanagari: substring (handles inflection)
            return kw_l.strip() in norm
        if " " in kw_l:                                  # Latin phrase
            return f" {kw_l} " in norm
        if kw_l in tokens:
            return True
        fuzzy = float(self.th.get("fuzzy_match", 0.85))
        return len(kw_l) >= 4 and any(
            len(t) >= 4 and difflib.SequenceMatcher(None, kw_l, t).ratio() >= fuzzy for t in tokens)

    def _lexicon_hits(self, words, norm, tokens):
        return [w for w in words if self._hit(w, norm, tokens)]

    def _ticket(self, case):
        h = hashlib.sha1(f"{case.get('id')}|{case.get('text')}".encode("utf-8")).hexdigest()[:8]
        return f"GRV-{h.upper()}"

    # ----------------------------------------------------------------- main
    def process(self, case: dict) -> dict:
        text = case.get("text")
        ticket = self._ticket(case)
        out = {"ticket": ticket, "language": None, "language_confidence": 0.0, "department": None,
               "department_name": None, "route_confidence": 0.0, "priority": "normal",
               "sensitive_categories": [], "flags": [], "human_required": False,
               "assigned_to": "auto-queue", "auto_reply": None, "draft_reply": None,
               "final_decision": None, "confidence": 0.0, "reasons": []}

        def handover(reason, flag=None):
            out["human_required"] = True
            if out["assigned_to"] == "auto-queue":
                out["assigned_to"] = "grievance cell officer"
            if flag:
                out["flags"].append(flag)
            out["reasons"].append(reason)

        # 1. Input validation (failure behaviour: escalate, never crash)
        if text is None or not str(text).strip():
            handover("Empty or missing complaint text; officer to contact citizen.", "empty_input")
            return self._finish(case, out)
        text = str(text)
        max_chars = int(self.th.get("max_chars", 2000))
        if len(text) > max_chars:
            out["flags"].append("truncated")
            out["reasons"].append(f"Input longer than {max_chars} characters; only the start was analysed.")
            text_for_ai = text[:max_chars]
        else:
            text_for_ai = text
        if find_pii(text):
            out["flags"].append("pii_detected_and_masked")

        norm = normalize(text_for_ai)
        tokens = set(norm.split())

        # 2. Understand (language) - adapter may fail
        try:
            if case.get("_inject_fault") == "translator":
                raise FaultInjected("translation service unavailable")
            lang, evidence = self.langid.identify(text_for_ai)
        except Exception as e:
            handover(f"Language service failed ({e}); routed to officer.", "translator_failure")
            return self._finish(case, out)
        support = float(self.lang_support.get(lang, 0.0))
        out["language"], out["language_confidence"] = lang, round(support * evidence, 2)
        if lang in ("unsupported", "unknown"):
            handover(f"Language '{lang}' not supported by the bot; officer to handle.", "unsupported_language")

        # 3. Classify department
        try:
            if case.get("_inject_fault") == "classifier":
                raise FaultInjected("classifier unavailable")
            scores = {d: len(self._lexicon_hits(v["keywords"], norm, tokens)) for d, v in self.depts.items()}
        except Exception as e:
            handover(f"Classifier failed ({e}); routed to officer.", "classifier_failure")
            return self._finish(case, out)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        (d1, s1), (_, s2) = ranked[0], ranked[1]
        if s1 > 0:
            strength = 1 - 0.5 ** s1
            margin = (s1 - s2) / s1
            route_conf = strength * (0.5 + 0.5 * margin)
            out["department"], out["department_name"] = d1, self.depts[d1]["name"]
            out["reasons"].append(f"Matched {s1} keyword(s) for {self.depts[d1]['name']}"
                                  + (f"; {s2} for the next department" if s2 else "") + ".")
        else:
            route_conf = 0.0
            out["reasons"].append("No department keywords matched.")
        out["route_confidence"] = round(route_conf, 2)
        out["confidence"] = round(min(route_conf, out["language_confidence"]), 2)

        # 4. Safety screen (on full text, not truncated text)
        full_norm, full_tokens = normalize(text), set(normalize(text).split())
        for cat, words in self.matrix.get("sensitive", {}).items():
            if self._lexicon_hits(words, full_norm, full_tokens):
                out["sensitive_categories"].append(cat)
        if self._lexicon_hits(self.matrix.get("urgency", []), norm, tokens):
            out["priority"] = "high"
            out["reasons"].append("Safety-related content (e.g. accident, fire, live wire) raised priority.")
        if any(rx.search(text) for rx in self.injection):
            handover("Text tries to instruct the system or claim priority; ignored and sent for review.",
                     "manipulation_attempt")
        if any(rx.search(text) for rx in self.data_req):
            handover("Request for another person's personal data; refused.", "data_request_refused")
        if self._lexicon_hits(self.matrix.get("abuse", []), norm, tokens):
            handover("Abusive language detected; officer to review the underlying grievance.", "abusive_language")

        if out["sensitive_categories"]:
            out["priority"] = "critical"
            out["human_required"] = True
            out["assigned_to"] = "designated sensitive-case officer"
            out["reasons"].append(f"Sensitive content ({', '.join(out['sensitive_categories'])}); "
                                  "no automated handling.")
            if "corruption" in out["sensitive_categories"]:
                out["flags"].append("protect_complainant_identity")

        # 5. Confidence check
        if not out["human_required"]:
            if out["language_confidence"] < float(self.th.get("language_confidence", 0.6)):
                handover("Low confidence in understanding the language; officer to check.", "low_language_confidence")
            elif route_conf < float(self.th.get("route_confidence", 0.55)):
                handover("Low confidence in department routing; officer to check.", "low_route_confidence")

        return self._finish(case, out)

    def _finish(self, case, out):
        t = self.tpl
        fields = {"ticket": out["ticket"], "department_name": out["department_name"] or "",
                  "sla_days": self.depts.get(out["department"], {}).get("sla_days", "")}
        if "data_request_refused" in out["flags"]:
            out["auto_reply"] = t["refusal"].format(**fields)
        elif out["sensitive_categories"]:
            out["auto_reply"] = t["sensitive"].format(**fields)
            if "self_harm" in out["sensitive_categories"]:
                out["auto_reply"] += " " + t["self_harm_addon"]
        elif out["human_required"]:
            out["auto_reply"] = t["handover"].format(**fields)
        else:
            out["auto_reply"] = t["ack"].format(**fields)
        if out["department"] and not out["sensitive_categories"]:
            out["draft_reply"] = t["draft"].format(**fields)
        if not out["reasons"]:
            out["reasons"].append("No issues found.")
        self.audit.record("grievance", case.get("id"), {**out, "complaint_text": mask(case.get("text"))})
        return out

"""PII detection and masking (DPDP-minded). Applied to everything written to logs or reports."""
import re

PATTERNS = {
    "aadhaar": re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
    "phone": re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
}


def find_pii(text) -> list:
    if not text:
        return []
    return [kind for kind, rx in PATTERNS.items() if rx.search(str(text))]


def mask(text, extra_terms=()):
    """Mask regex PII plus known personal values (e.g. names) passed in extra_terms."""
    if text is None:
        return None
    out = str(text)
    for kind, rx in PATTERNS.items():
        out = rx.sub(f"[{kind.upper()}]", out)
    for term in extra_terms:
        if term and len(str(term)) > 2:
            out = re.sub(re.escape(str(term)), "[NAME]", out, flags=re.IGNORECASE)
    return out


def mask_obj(obj, extra_terms=()):
    """Recursively mask strings inside dicts/lists."""
    if isinstance(obj, str):
        return mask(obj, extra_terms)
    if isinstance(obj, dict):
        return {k: mask_obj(v, extra_terms) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_obj(v, extra_terms) for v in obj]
    return obj

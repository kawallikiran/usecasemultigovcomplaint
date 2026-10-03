"""Pluggable language/model adapters.

The MVP default is fully OFFLINE (lexicon based), so no citizen text leaves the machine.
For a sovereign deployment, implement the same interface against Bhashini (ASR/translation) and an
India-hosted LLM, keep data on Indian infrastructure, and re-run the critic suite to compare.
"""
import re
import unicodedata

DEVANAGARI = re.compile(r"[\u0900-\u097F]")
LATIN = re.compile(r"[A-Za-z]")


def other_script_letters(text):
    n = 0
    for ch in text:
        if ch.isalpha() and not DEVANAGARI.match(ch) and not LATIN.match(ch):
            n += 1
    return n


class OfflineLanguageID:
    """Script + marker based identification. Returns (language, evidence 0..1)."""

    def __init__(self, markers: dict):
        self.cg = markers.get("cg", [])
        self.hinglish = set(markers.get("hinglish", []))

    def identify(self, text: str):
        deva = len(DEVANAGARI.findall(text))
        lat = len(LATIN.findall(text))
        oth = other_script_letters(text)
        total = deva + lat + oth
        if total == 0:
            return "unknown", 0.0
        if oth / total > 0.3:
            return "unsupported", 0.0
        if deva and lat and min(deva, lat) / total > 0.15:
            return "mixed", 0.85
        if deva >= lat:
            padded = normalize(text)  # " tok tok " -> whole-token / phrase matching
            hits = sum(1 for m in self.cg if f" {m.strip()} " in padded)
            return ("cg", min(1.0, 0.75 + 0.1 * hits)) if hits else ("hi", 0.95)
        tokens = re.findall(r"[a-z]+", text.lower())
        hh = sum(1 for t in tokens if t in self.hinglish)
        if hh >= 2:
            return "hinglish", min(1.0, 0.6 + 0.1 * hh)
        return "en", 0.95


class BhashiniTranslator:  # pragma: no cover - integration stub
    """Placeholder: call Bhashini ULCA/Dhruva pipelines for ASR + translation here.
    Must return (language_code, confidence). Disabled in the MVP (no network, no data sharing)."""

    def identify(self, text: str):
        raise NotImplementedError("Configure Bhashini credentials and endpoint for a sovereign deployment.")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = text.lower()
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)          # "paaaani" -> "paani"
    text = re.sub(r"[^\w\u0900-\u097F]+", " ", text)     # punctuation, emoji, danda
    return f" {' '.join(text.split())} "

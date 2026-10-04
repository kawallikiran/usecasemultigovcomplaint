"""Language identification and text normalisation (offline).

The MVP identifies languages by Unicode SCRIPT, then separates languages that share a script with marker words
(Hindi / Marathi / Chhattisgarhi in Devanagari; Bengali / Assamese in Bengali script). This is transparent and
runs with no network, but it cannot tell apart every language that shares a script (see languages.yaml).
For a sovereign deployment, implement the same identify() interface with Bhashini's language detection and
re-run the critic suite to compare.
"""
import re
import unicodedata

# Unicode blocks -> script name
SCRIPTS = [
    ("devanagari", 0x0900, 0x097F), ("bengali", 0x0980, 0x09FF), ("gurmukhi", 0x0A00, 0x0A7F),
    ("gujarati", 0x0A80, 0x0AFF), ("odia", 0x0B00, 0x0B7F), ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F), ("kannada", 0x0C80, 0x0CFF), ("malayalam", 0x0D00, 0x0D7F),
    ("arabic", 0x0600, 0x06FF), ("arabic", 0x0750, 0x077F), ("arabic", 0xFB50, 0xFDFF), ("arabic", 0xFE70, 0xFEFF),
    ("olchiki", 0x1C50, 0x1C7F), ("meetei", 0xABC0, 0xABFF), ("meetei", 0xAAE0, 0xAAFF),
]
SCRIPT_LANG = {"gurmukhi": "pa", "gujarati": "gu", "odia": "or", "tamil": "ta", "telugu": "te",
               "kannada": "kn", "malayalam": "ml", "arabic": "ur", "olchiki": "sat", "meetei": "mni"}
DEVANAGARI = re.compile(r"[\u0900-\u097F]")
LATIN = re.compile(r"[A-Za-z]")
# characters kept by normalize(): word characters + every Indic / Perso-Arabic block above (incl. vowel signs)
_KEEP = "".join(f"\\u{a:04x}-\\u{b:04x}" for _, a, b in SCRIPTS)
_STRIP = re.compile(rf"[^\w{_KEEP}]+")
_SENTENCE_MARKS = re.compile(r"[\u0964\u0965\u06d4\u060c\u061f]")    # danda, double danda, Urdu stop/comma/?


def script_of(ch):
    o = ord(ch)
    for name, a, b in SCRIPTS:
        if a <= o <= b:
            return name
    if ch.isascii() and ch.isalpha():
        return "latin"
    return "other" if ch.isalpha() else None


def normalize(text: str) -> str:
    """NFC, lower case, no zero-width joiners, '\u0964' and similar treated as spaces, stretched letters shortened.
    Vowel signs of every Indic script are kept, so words stay whole."""
    text = unicodedata.normalize("NFC", text or "").replace("\u200c", "").replace("\u200d", "").lower()
    text = _SENTENCE_MARKS.sub(" ", text)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)            # "paaaani" -> "paani"
    text = _STRIP.sub(" ", text)
    return f" {' '.join(text.split())} "


class OfflineLanguageID:
    """Returns (language code, evidence 0..1)."""

    def __init__(self, markers: dict):
        self.cg = [m.strip() for m in markers.get("cg", [])]
        self.mr = [m.strip() for m in markers.get("mr", [])]
        self.hinglish = set(markers.get("hinglish", []))
        self.as_letters = set(markers.get("as_letters", ["ৰ", "ৱ"]))

    def identify(self, text: str):
        counts = {}
        for ch in text:
            s = script_of(ch)
            if s:
                counts[s] = counts.get(s, 0) + 1
        total = sum(counts.values())
        if not total:
            return "unknown", 0.0
        if counts.get("other", 0) / total > 0.3:
            return "unsupported", 0.0
        indic = {k: v for k, v in counts.items() if k not in ("latin", "other")}
        lat = counts.get("latin", 0)
        if indic:
            main = max(indic, key=indic.get)
            if lat and min(lat, indic[main]) / total > 0.15:
                return "mixed", 0.85
        else:
            main = "latin"
        if main == "devanagari":
            padded = normalize(text)
            cg = sum(1 for m in self.cg if f" {m} " in padded)
            mr = sum(1 for m in self.mr if f" {m} " in padded)
            if mr > cg and mr:
                return "mr", min(1.0, 0.75 + 0.1 * mr)
            if cg:
                return "cg", min(1.0, 0.75 + 0.1 * cg)
            return "hi", 0.95
        if main == "bengali":
            return ("as", 0.9) if any(ch in self.as_letters for ch in text) else ("bn", 0.9)
        if main in SCRIPT_LANG:
            return SCRIPT_LANG[main], 0.95 if main != "arabic" else 0.9
        tokens = re.findall(r"[a-z]+", text.lower())
        hh = sum(1 for t in tokens if t in self.hinglish)
        if hh >= 2:
            return "hinglish", min(1.0, 0.6 + 0.1 * hh)
        return "en", 0.95


class BhashiniTranslator:  # pragma: no cover - integration stub
    """Placeholder: call Bhashini (ULCA / Dhruva) language detection, ASR and translation here, returning
    (language_code, confidence). Disabled in the MVP (no network, no data sharing)."""

    def identify(self, text: str):
        raise NotImplementedError("Configure Bhashini credentials and endpoint for a sovereign deployment.")

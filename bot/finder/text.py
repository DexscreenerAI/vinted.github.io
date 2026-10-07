import re
import unicodedata
from typing import Optional

_PRICE_RE = re.compile(r"(\d{1,3}(?:[   .]\d{3})*|\d+)(?:[,.](\d{1,2}))?\s?€")
_AD_ID_RE = re.compile(r"leboncoin\.fr/(?:ad/[\w-]+/|vi/|[\w-]+/)(\d{8,12})")
_LOT_RE = re.compile(r"\blot\s+(?:de\s+)?(\d{1,3})\b|\b(\d{1,3})\s+(?:pieces|articles|vetements|pulls|t-?shirts|jeans|vestes|polos|chemises|jeux|cartes)\b")


# Variantes d'écriture ramenées à une seule forme (après normalisation)
_ALIASES = {
    "doc martens": "dr martens", "doc marten": "dr martens", "dr marten s": "dr martens",
    "rayban": "ray ban", "gameboy": "game boy", "g shock": "gshock", "levis s": "levis",
    "the north face": "north face", "tnf": "north face", "arc teryx": "arcteryx",
    "carhart": "carhartt", "carharrt": "carhartt", "docs martens": "dr martens", "dr martin": "dr martens",
    "dr martins": "dr martens", "ciree": "cire", "arcterix": "arcteryx", "torsades": "torsade",
}


def normalize(text: str) -> str:
    """Minuscules, sans accents ni ponctuation : « Ray-Ban », « ray ban » et « RAYBAN » se comparent pareil."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[\'’`´]", "", text)          # arc'teryx -> arcteryx, levi's -> levis
    text = re.sub(r"[-_.,;:!?()\[\]/+*\"]", " ", text)  # xt-6 -> xt 6, dr. martens -> dr martens
    text = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", text)  # dw5600 = dw-5600 = dw 5600
    text = re.sub(r"\s+", " ", text).strip()
    for src, dst in _ALIASES.items():
        text = re.sub(rf"(?<![a-z0-9]){src}(?![a-z0-9])", dst, text)
    return text


def parse_price(text: str) -> Optional[float]:
    """Premier prix en euros trouvé dans le texte ("1 200 €", "35,50 €", "35€")."""
    m = _PRICE_RE.search(text)
    if not m:
        return None
    whole = re.sub(r"[   .]", "", m.group(1))
    cents = m.group(2) or "0"
    return float(f"{whole}.{cents}")


def ad_id(url: str) -> Optional[str]:
    m = _AD_ID_RE.search(url or "")
    return m.group(1) if m else None


def lot_size(title: str) -> Optional[int]:
    """Nombre de pièces annoncé dans le titre ("lot de 12", "15 pulls"), sinon None."""
    m = _LOT_RE.search(normalize(title))
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    return n if 1 < n <= 500 else None


def contains(haystack_norm: str, word) -> bool:
    """Mot-clé présent en tant que mot (ou expression) entier. Une liste = au moins un des mots."""
    if isinstance(word, (list, tuple)):
        return any(contains(haystack_norm, w) for w in word)
    w = normalize(str(word))
    if not w:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", haystack_norm) is not None


def find_pos(haystack_norm: str, word) -> int:
    """Position du mot-clé (ou du premier trouvé d'une liste) dans le texte normalisé, -1 si absent."""
    if isinstance(word, (list, tuple)):
        found = [p for p in (find_pos(haystack_norm, w) for w in word) if p >= 0]
        return min(found) if found else -1
    w = normalize(str(word))
    m = re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", haystack_norm) if w else None
    return m.start() if m else -1

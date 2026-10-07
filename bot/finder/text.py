import re
import unicodedata
from typing import Optional

_PRICE_RE = re.compile(r"(\d{1,3}(?:[   .]\d{3})*|\d+)(?:[,.](\d{1,2}))?\s?€")
_AD_ID_RE = re.compile(r"leboncoin\.fr/(?:ad/[\w-]+/|vi/|[\w-]+/)(\d{8,12})")
_LOT_RE = re.compile(r"\blot\s+(?:de\s+)?(\d{1,3})\b|\b(\d{1,3})\s+(?:pieces|articles|vetements|pulls|t-?shirts|jeans|vestes|polos|chemises|jeux|cartes)\b")


def normalize(text: str) -> str:
    """Minuscules, sans accents, espaces simplifiés : pour comparer des mots-clés."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


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


def contains(haystack_norm: str, word: str) -> bool:
    """Mot-clé présent en tant que mot (ou expression) entier."""
    w = normalize(word)
    return re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", haystack_norm) is not None

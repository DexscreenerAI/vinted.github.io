from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    """Une annonce Leboncoin extraite d'un email d'alerte (ou saisie à la main)."""

    title: str
    price: Optional[float]
    url: str = ""
    image: str = ""
    location: str = ""
    source: str = "leboncoin"   # leboncoin, ebay, interencheres, kleinanzeigen…
    ends_at: str = ""           # fin d'enchère (ISO 8601, UTC) si c'est une enchère
    buy_cost: Optional[float] = None  # coût d'achat total connu (prix + port + frais) ; None = calcul Leboncoin

    @property
    def key(self) -> str:
        """Identifiant stable pour ne pas notifier deux fois la même annonce."""
        from .text import ad_id

        if self.source == "leboncoin":
            return ad_id(self.url) or f"{self.title.lower()}|{self.price}"
        if self.source == "ebay":  # même annonce eBay, quelle que soit la source (API, email, extension, .fr/.de)
            import re
            m = re.search(r"/itm/(?:[^/?#]+/)?(\d{9,15})", self.url or "")
            if m:
                return f"ebay:{m.group(1)}"
        return f"{self.source}:{self.url or self.title.lower() + '|' + str(self.price)}"


@dataclass
class Deal:
    """Une annonce qui correspond à une règle, avec son estimation de revente."""

    listing: Listing
    rule_name: str
    pieces: int
    buy_cost: float          # prix + frais d'achat (protection + port)
    est_resale: float        # revente brute estimée sur Vinted
    net_profit: float        # après cotisations / impôt / emballage
    ratio: float             # multiplicateur = revente estimée / coût d'achat
    notes: list = field(default_factory=list)
    category: str = "Autre"

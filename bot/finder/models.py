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

    @property
    def key(self) -> str:
        """Identifiant stable pour ne pas notifier deux fois la même annonce."""
        from .text import ad_id

        return ad_id(self.url) or f"{self.title.lower()}|{self.price}"


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

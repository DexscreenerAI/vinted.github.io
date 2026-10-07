"""Règles de recherche (config.yaml) et calcul du multiplicateur / bénéfice net."""

from dataclasses import dataclass, field
from typing import List, Optional

from .models import Deal, Listing
from .text import contains, lot_size, normalize


@dataclass
class Costs:
    buy_fee_fixed: float = 0.70     # protection paiement sécurisé Leboncoin
    buy_fee_rate: float = 0.05
    buy_shipping: float = 3.50      # port payé à l'achat (0 si main propre)
    packaging: float = 0.50         # emballage par colis revendu
    resale_discount: float = 0.85   # prix demandés Vinted > prix réellement vendus
    tax_rate: float = 0.134         # URSSAF 12,3 % + formation 0,1 % + versement libératoire 1 %


@dataclass
class Rule:
    name: str
    ref_price: float                # prix de revente Vinted habituel (par pièce si lot)
    category: str = "Autre"
    all: List = field(default_factory=list)        # tous ces mots doivent être présents ([a, b] = a ou b)
    any: List[str] = field(default_factory=list)   # au moins un de ces mots
    none: List[str] = field(default_factory=list)  # aucun de ces mots
    max_buy: Optional[float] = None
    lot: bool = False
    default_pieces: int = 5         # si "lot" sans nombre dans le titre
    sellable_rate: float = 0.7      # part des pièces d'un lot réellement vendable
    min_ratio: Optional[float] = None
    min_profit: Optional[float] = None

    def matches(self, title: str, global_none: List[str]) -> bool:
        t = normalize(title)
        if any(contains(t, w) for w in self.none + global_none):
            return False
        if not all(contains(t, w) for w in self.all):
            return False
        return not self.any or any(contains(t, w) for w in self.any)


@dataclass
class Config:
    rules: List[Rule]
    costs: Costs = field(default_factory=Costs)
    min_ratio: float = 2.5
    min_profit: float = 15.0
    exclude: List[str] = field(default_factory=list)
    hand_delivery: bool = False     # True = on suppose une remise en main propre (pas de frais d'achat)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        return cls(
            rules=[Rule(**r) for r in data.get("rules", [])],
            costs=Costs(**data.get("costs", {})),
            min_ratio=data.get("min_ratio", 2.5),
            min_profit=data.get("min_profit", 15.0),
            exclude=data.get("exclude", []),
            hand_delivery=data.get("hand_delivery", False),
        )


def reload_if_changed(cfg: Config, path: str) -> bool:
    """Relit config.yaml s'il a été modifié depuis le dernier chargement (sans redémarrer)."""
    import os
    import yaml

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    if getattr(cfg, "_mtime", None) == mtime:
        return False
    with open(path, encoding="utf-8") as f:
        fresh = Config.from_dict(yaml.safe_load(f))
    cfg.__dict__.update(fresh.__dict__)
    cfg._mtime = mtime
    return True


def evaluate(listing: Listing, rule: Rule, cfg: Config, hand_delivery: Optional[bool] = None) -> Deal:
    c = cfg.costs
    hand = cfg.hand_delivery if hand_delivery is None else hand_delivery
    price = listing.price or 0.0
    notes = []

    pieces = 1
    if rule.lot:
        announced = lot_size(listing.title)
        pieces = announced or rule.default_pieces
        if not announced:
            notes.append(f"nombre de pièces inconnu, estimé à {pieces}")
    sold = max(1, round(pieces * (rule.sellable_rate if rule.lot else 1)))

    if listing.buy_cost is not None:  # coût fourni par la source (eBay : port, enchères : frais acheteur…)
        buy_cost = listing.buy_cost
    else:
        buy_cost = price if hand else price + c.buy_fee_fixed + c.buy_fee_rate * price + c.buy_shipping
    est_resale = rule.ref_price * c.resale_discount * sold
    net_profit = est_resale * (1 - c.tax_rate) - c.packaging * sold - buy_cost
    ratio = est_resale / buy_cost if buy_cost > 0 else float("inf")

    if rule.max_buy is not None and price > rule.max_buy:
        notes.append(f"au-dessus du prix max ({rule.max_buy:.0f} €)")

    return Deal(listing, rule.name, sold, round(buy_cost, 2), round(est_resale, 2),
                round(net_profit, 2), round(ratio, 2), notes, rule.category)


def is_good(deal: Deal, rule: Rule, cfg: Config) -> bool:
    if deal.listing.price is None or deal.listing.price <= 0:
        return False
    if rule.max_buy is not None and deal.listing.price > rule.max_buy:
        return False
    min_ratio = rule.min_ratio if rule.min_ratio is not None else cfg.min_ratio
    min_profit = rule.min_profit if rule.min_profit is not None else cfg.min_profit
    return deal.ratio >= min_ratio and deal.net_profit >= min_profit


def find_deals(listing: Listing, cfg: Config, only_good: bool = True,
               hand_delivery: Optional[bool] = None) -> List[Deal]:
    """Toutes les règles qui correspondent à l'annonce, meilleure affaire en premier."""
    deals = []
    for rule in cfg.rules:
        if rule.matches(listing.title, cfg.exclude):
            deal = evaluate(listing, rule, cfg, hand_delivery)
            if not only_good or is_good(deal, rule, cfg):
                deals.append(deal)
    return sorted(deals, key=lambda d: d.ratio, reverse=True)

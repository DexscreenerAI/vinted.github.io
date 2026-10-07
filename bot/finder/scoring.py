"""Règles de recherche (config.yaml) et calcul du multiplicateur / bénéfice net."""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .models import Deal, Listing
from .text import contains, find_pos, lot_size, normalize


@dataclass
class Costs:
    buy_fee_fixed: float = 0.70     # protection paiement sécurisé Leboncoin
    buy_fee_rate: float = 0.05
    buy_shipping: float = 3.50      # port payé à l'achat (0 si main propre)
    packaging: float = 0.50         # emballage par colis revendu
    resale_discount: float = 0.85   # prix demandés Vinted > prix réellement vendus
    tax_rate: float = 0.134         # URSSAF 12,3 % + formation 0,1 % + versement libératoire 1 %


# Défauts annoncés dans le titre (ignorés s'ils sont niés : « sans tache », « aucun défaut »)
DEFECTS = ["abime", "abimee", "abimes", "tache", "taches", "tachee", "troue", "trou", "trous", "dechire", "dechiree",
           "raye", "rayee", "rayures", "rayure", "fissure", "fissuree", "casse", "cassee", "etat moyen", "etat correct",
           "mauvais etat", "etat d usage", "a restaurer", "defaut", "defauts", "jauni", "jaunie", "decolore", "bouloche",
           "usure", "tres use", "use", "manque", "incomplet", "incomplete", "pour pieces"]
_NEGATIONS = ("sans", "aucun", "aucune", "pas de", "zero", "0", "ni", "pas d", "jamais")
CONDITION_RANK = {"neuf": 4, "tres_bon": 3, "bon": 2, "usage": 1, "mauvais": 0, "inconnu": None}


def has_defect(title_norm: str) -> bool:
    for w in DEFECTS:
        pos = find_pos(title_norm, w)
        while pos >= 0:
            before = title_norm[max(0, pos - 12):pos]
            if not any(re.search(rf"(?<![a-z]){n} $", before) for n in _NEGATIONS):
                return True
            nxt = re.search(rf"(?<![a-z0-9]){re.escape(normalize(w))}(?![a-z0-9])", title_norm[pos + 1:])
            pos = pos + 1 + nxt.start() if nxt else -1
    return False


# Accessoires : en tête de titre (« Étui Contax T2 », « Flash Canon pour AE-1 ») ou avant « pour <modèle> »,
# l'annonce vend un accessoire, pas l'objet de la règle — dont la cote ne s'applique donc pas.
ACCESSORIES = ["patch", "ecusson", "coque", "chargeur", "cable", "housse", "etui", "sacoche", "pochette",
               "objectif", "flash", "dos dateur", "data back", "winder", "power winder", "moteur", "telecommande",
               "batterie", "manette", "boite vide", "boite seule", "notice", "mode d emploi", "bracelet", "dragonne",
               "courroie", "sangle", "bouchon", "pare soleil", "filtre", "pellicule", "pellicules", "sticker",
               "autocollant", "porte cles", "lacets", "semelles", "ecran", "adaptateur", "support", "accessoire",
               "accessoires", "piece", "pieces"]


def is_accessory(title_norm: str, rule_words) -> bool:
    words = [w for w in ACCESSORIES if normalize(w) not in {normalize(str(x)) for x in rule_words}]
    head = " ".join(title_norm.split()[:2])
    if any(contains(head, w) for w in words):
        return True
    pos_model = find_pos(title_norm, rule_words) if rule_words else -1
    pos_pour = find_pos(title_norm, ["pour", "compatible", "fits"])
    return 0 <= pos_pour < pos_model


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
    # Exclusion conditionnelle : un de ces mots exclut l'annonce, SAUF si un mot « unless » est aussi présent.
    # Ex. console : « 3 jeux Game Boy Color » = des jeux, pas la console ; « Game Boy Color + 3 jeux » = la console.
    none_unless: dict = field(default_factory=dict)
    search: str = ""                # requête eBay ; vide = déduite des mots-clés
    variants: List[str] = field(default_factory=list)  # requêtes eBay en plus (fautes courantes : carhart…)

    def matches(self, title: str, global_none: List[str]) -> bool:
        t = normalize(title)
        if any(contains(t, w) for w in self.none + global_none):
            return False
        if not all(contains(t, w) for w in self.all):
            return False
        if self.any and not any(contains(t, w) for w in self.any):
            return False
        flat = [x for w in self.all for x in (w if isinstance(w, list) else [w])] + list(self.any)
        nu = self.none_unless or {}
        if nu and not any(contains(t, w) for w in nu.get("unless", [])):
            # le mot (« jeux ») placé AVANT l'objet (« 3 jeux Game Boy Color ») = c'est lui qui est vendu ;
            # placé après (« Game Boy Color + jeu Tetris »), c'est un bonus avec l'objet
            pos_word, pos_model = find_pos(t, nu.get("words", [])), find_pos(t, flat)
            if pos_word >= 0 and (pos_model < 0 or pos_word < pos_model):
                return False
        return not is_accessory(t, flat)


@dataclass
class Config:
    rules: List[Rule]
    costs: Costs = field(default_factory=Costs)
    min_ratio: float = 2.5
    min_profit: float = 15.0
    exclude: List[str] = field(default_factory=list)
    hand_delivery: bool = False     # True = on suppose une remise en main propre (pas de frais d'achat)
    etat_minimum: str = "tres_bon"  # neuf | tres_bon | bon | tous

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        data = dict(data or {})
        data.pop("config_version", None)
        return cls(
            rules=[Rule(**r) for r in data.get("rules", [])],
            costs=Costs(**data.get("costs", {})),
            min_ratio=data.get("min_ratio", 2.5),
            min_profit=data.get("min_profit", 15.0),
            exclude=data.get("exclude", []),
            hand_delivery=data.get("hand_delivery", False),
            etat_minimum=str(data.get("etat_minimum", "tres_bon")),
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
    ratio = est_resale / buy_cost if buy_cost > 0 else 0.0  # prix 0 € : pas de ratio (évite Infinity en JSON)

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
    if cfg.etat_minimum in ("neuf", "tres_bon") and has_defect(normalize(listing.title)):
        return []  # l'utilisateur n'achète que du très bon état
    """Toutes les règles qui correspondent à l'annonce, meilleure affaire en premier."""
    deals = []
    for rule in cfg.rules:
        if rule.matches(listing.title, cfg.exclude):
            deal = evaluate(listing, rule, cfg, hand_delivery)
            if not only_good or is_good(deal, rule, cfg):
                deals.append(deal)
    return sorted(deals, key=lambda d: d.ratio, reverse=True)

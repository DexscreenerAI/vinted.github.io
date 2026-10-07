"""Liens de recherche pour chaque article surveillé (Leboncoin, Vinted, eBay).

Servent à créer ses alertes et au bouton « Recherche suivante » de l'extension : c'est
l'utilisateur qui clique, l'extension analyse ensuite la page affichée.
"""

from urllib.parse import quote, urlencode

from .ebay_source import rule_queries
from .scoring import Rule

# Articles proposés en premier (meilleur compromis marge × rotation × fréquence)
TOP = ["Carhartt Detroit jacket", "Game Boy Color console", "Game Boy Advance SP", "Olympus mju II",
       "Canon AE-1 / AE-1 Program", "Levi's 501 Made in USA", "Ray-Ban B&L vintage", "The North Face Nuptse",
       "Lot vêtements marque homme", "Classeur cartes Pokémon WOTC 1999-2002"]


def lbc_url(rule: Rule) -> str:
    """Recherche Leboncoin : mots-clés, plus récentes d'abord, plafonnée au prix max."""
    params = {"text": rule_queries(rule)[0], "sort": "time", "order": "desc"}
    if rule.max_buy is not None:
        params["price"] = f"min-{rule.max_buy:g}"
    return "https://www.leboncoin.fr/recherche?" + urlencode(params, quote_via=quote)


def vinted_url(rule: Rule) -> str:
    params = {"search_text": rule_queries(rule)[0], "order": "newest_first"}
    if rule.max_buy is not None:
        params["price_to"] = f"{rule.max_buy:g}"
    return "https://www.vinted.fr/catalog?" + urlencode(params, quote_via=quote)


def ebay_url(rule: Rule) -> str:
    params = {"_nkw": rule_queries(rule)[0], "_sop": "10"}
    if rule.max_buy is not None:
        params["_udhi"] = f"{rule.max_buy:g}"
    return "https://www.ebay.fr/sch/i.html?" + urlencode(params)


def rules_with_links(rules) -> list:
    out = [{"name": r.name, "category": r.category, "query": rule_queries(r)[0], "max_buy": r.max_buy,
            "ref_price": r.ref_price, "lot": r.lot, "top": r.name in TOP,
            "lbc_url": lbc_url(r), "vinted_url": vinted_url(r), "ebay_url": ebay_url(r)} for r in rules]
    return sorted(out, key=lambda r: (not r["top"], TOP.index(r["name"]) if r["top"] else 0))

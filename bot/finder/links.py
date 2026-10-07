"""Liens de recherche prêts à l'emploi pour créer ses alertes (Leboncoin, eBay) en un clic."""

from urllib.parse import urlencode

from .ebay_source import rule_queries
from .scoring import Rule


def lbc_url(rule: Rule) -> str:
    """Recherche Leboncoin triée par plus récentes, plafonnée au prix max de la règle."""
    params = {"text": rule_queries(rule)[0], "sort": "time", "order": "desc"}
    if rule.max_buy is not None:
        params["price"] = f"min-{rule.max_buy:g}"
    return "https://www.leboncoin.fr/recherche?" + urlencode(params)


def ebay_url(rule: Rule) -> str:
    """Recherche ebay.fr : objets en France, plus récents d'abord, plafonnée au prix max."""
    params = {"_nkw": rule_queries(rule)[0], "_sop": "10", "LH_PrefLoc": "1"}
    if rule.max_buy is not None:
        params["_udhi"] = f"{rule.max_buy:g}"
    return "https://www.ebay.fr/sch/i.html?" + urlencode(params)


def rules_with_links(rules) -> list:
    return [{"name": r.name, "category": r.category, "query": rule_queries(r)[0], "max_buy": r.max_buy,
             "ref_price": r.ref_price, "lot": r.lot, "lbc_url": lbc_url(r), "ebay_url": ebay_url(r)} for r in rules]

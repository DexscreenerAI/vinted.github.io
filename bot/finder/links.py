"""Liens de recherche pour créer ses alertes (Leboncoin, eBay) en un clic.

Volontairement minimaux (seulement les mots-clés) : les paramètres de prix et de tri dans
l'adresse ont été refusés par les sites. Le prix max et le tri se règlent sur la page du site.
"""

from urllib.parse import quote, urlencode

from .ebay_source import rule_queries
from .scoring import Rule


def lbc_url(rule: Rule) -> str:
    return "https://www.leboncoin.fr/recherche?" + urlencode({"text": rule_queries(rule)[0]}, quote_via=quote)


def ebay_url(rule: Rule) -> str:
    return "https://www.ebay.fr/sch/i.html?" + urlencode({"_nkw": rule_queries(rule)[0]})


def rules_with_links(rules) -> list:
    return [{"name": r.name, "category": r.category, "query": rule_queries(r)[0], "max_buy": r.max_buy,
             "ref_price": r.ref_price, "lot": r.lot, "lbc_url": lbc_url(r), "ebay_url": ebay_url(r)} for r in rules]

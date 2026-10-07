"""Boucle principale : lecture des alertes, évaluation, enregistrement."""

import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import ebay_source, email_source
from .models import Listing
from .notify import send
from .scoring import Config, find_deals, is_good, reload_if_changed
from .text import contains, normalize
from .store import Seen


def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: str) -> Config:
    cfg = Config(rules=[])
    reload_if_changed(cfg, path)
    if not getattr(cfg, "_mtime", None):
        raise SystemExit(f"Fichier de configuration introuvable : {path}")
    return cfg


WAKE = threading.Event()  # « Vérifier maintenant » : réveille la boucle sans attendre


# Sources d'annonces : (nom affiché, configurée ?, récupération des annonces)
SOURCES = [
    ("Emails d'alerte", email_source.configured, email_source.fetch_listings),
    ("eBay", ebay_source.configured, ebay_source.fetch_listings),
]


# Achat sur Vinted (pour revendre sur Vinted) : protection acheteur 0,70 € + 5 % et envoi (~3 € en point relais)
VINTED_FEE_FIXED = 0.70
VINTED_FEE_RATE = 0.05
VINTED_SHIPPING = 3.00


_SITE_DOMAINS = {"leboncoin": ("leboncoin.fr",), "vinted": ("vinted.fr", "vinted.net"),
                 "ebay": ("ebay.fr", "ebay.de", "ebay.it", "ebay.es", "ebay.com", "ebayimg.com")}
_IMG_DOMAINS = ("leboncoin.fr", "vinted.net", "vinted.fr", "ebayimg.com", "ebay.com")


def _safe_url(url: str, domains) -> str:
    """Adresse https:// d'un domaine attendu, sinon "" (bloque les liens javascript:… injectés)."""
    from urllib.parse import urlsplit
    url = str(url or "").strip()[:500]
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(host == d or host.endswith("." + d) for d in domains):
        return ""
    return url


def import_listings(items: list, source: str, cfg: Config, seen: Seen) -> dict:
    """Annonces envoyées par le bouton « Analyser cette page » (page ouverte par l'utilisateur).

    Toutes sont évaluées et renvoyées (même déjà vues) ; seules les nouvelles sont enregistrées,
    pour ne pas écraser le statut (achetée, ignorée…) d'une affaire déjà connue.
    """
    results = []
    for it in items[:300]:
        try:
            price = float(it.get("price"))
        except (TypeError, ValueError):
            continue
        title = str(it.get("title") or "").strip()[:200]
        if not title or price <= 0:
            continue
        url = _safe_url(it.get("url"), _SITE_DOMAINS.get(source, ()))
        if not url:
            continue
        listing = Listing(title=title, price=price, url=url, image=_safe_url(it.get("image"), _IMG_DOMAINS),
                          location=str(it.get("location") or "")[:100], source=source)
        if source == "vinted":
            listing.buy_cost = round(price + VINTED_FEE_FIXED + VINTED_FEE_RATE * price + VINTED_SHIPPING, 2)
        elif source == "ebay":  # frais de protection acheteurs eBay + port moyen (inconnu sur la page de résultats)
            from .ebay_source import buyer_protection_fee
            listing.buy_cost = round(price + buyer_protection_fee(price) + cfg.costs.buy_shipping, 2)
        best = best_deal(listing, cfg)
        if best is None:
            continue
        deal, good = best
        if seen.add(listing.key, listing.title, listing.price):
            register(deal, good, seen)
        results.append({"title": title, "price": price, "url": listing.url, "image": listing.image,
                        "rule": deal.rule_name, "category": deal.category, "ratio": deal.ratio,
                        "net_profit": deal.net_profit, "buy_cost": deal.buy_cost, "est_resale": deal.est_resale,
                        "good": good})
    results.sort(key=lambda r: (r["good"], r["ratio"]), reverse=True)
    return {"received": len(items), "matched": len(results), "good": sum(r["good"] for r in results),
            "source": source, "deals": results}


def notify_good(deal_row_or_deal) -> None:
    """Notification (Telegram ou console) d'une bonne affaire ; une erreur ne doit rien bloquer."""
    try:
        if isinstance(deal_row_or_deal, dict):  # affaire validée par l'IA (ligne de la base)
            d = deal_row_or_deal
            print(f"[bonne affaire] {d['title']} : {d['price']} € → x{d['ratio']}, +{d['net_profit']} € ({d['url']})")
            return
        send(deal_row_or_deal)
    except Exception as e:
        print(f"[notification] {e}", file=sys.stderr)


def register(deal, good: bool, seen: Seen) -> None:
    """Enregistre une nouvelle affaire. Si l'avis IA automatique est actif, une bonne affaire attend le
    verdict de Claude avant d'apparaître dans « Bonnes affaires » et d'être notifiée."""
    from . import ai
    seen.save_deal(deal, good)
    if good and ai.auto_enabled():
        seen.set_ai_pending(deal.listing.key, True)
        ai.enqueue(deal.listing.key)
    elif good:
        notify_good(deal)


def process(listings, cfg: Config, seen: Seen) -> int:
    """Évalue et enregistre les annonces jamais vues ; renvoie le nombre de bonnes affaires."""
    found = 0
    for listing in listings:
        if not listing.price or listing.price <= 0:
            continue
        if not seen.add(listing.key, listing.title, listing.price):
            continue
        best = best_deal(listing, cfg)
        if best is None:
            continue
        deal, good = best
        register(deal, good, seen)
        if good:
            found += 1
    return found


def run_once(cfg: Config, seen: Seen) -> int:
    """Interroge chaque source configurée. Une source en erreur n'empêche pas les autres."""
    found = 0
    sources = seen.status.setdefault("sources", {})
    active = [(name, fetch) for name, is_configured, fetch in SOURCES if is_configured()]
    if not active:
        seen.status.update(error="Aucune source configurée : cliquez sur « Réglages » (boîte mail ou eBay)")
        return 0
    errors = []
    for name, fetch in active:
        state = sources.setdefault(name, {})
        try:
            found += process(fetch(cfg, seen), cfg, seen)
            state.update(last_check=time.strftime("%H:%M"), error=None)
        except Exception as e:  # on ne veut pas que le bot s'arrête sur une erreur réseau
            state.update(error=str(e) or e.__class__.__name__)
            errors.append(f"{name} : {state['error']}")
            print(f"[erreur] {name} : {e}", file=sys.stderr)
    seen.status.update(last_check=time.strftime("%H:%M"), error="Source en erreur — " + " · ".join(errors) if errors else None)
    return found


def rescore(cfg: Config, seen: Seen) -> tuple:
    """Réévalue les affaires en attente avec les règles actuelles : celles qui ne correspondent plus
    (ex. un jeu coté comme une console) disparaissent. Renvoie (réévaluées, retirées)."""
    updated = removed = 0
    for d in seen.pending_deals():
        listing = Listing(title=d["title"], price=d["price"] or 0, url=d["url"] or "", image=d["image"] or "",
                          location=d["location"] or "", source=d.get("source") or "leboncoin",
                          ends_at=d.get("ends_at") or "")
        if listing.source != "leboncoin":  # coût d'achat calculé par la source (port, frais) : on le garde
            listing.buy_cost = d["buy_cost"]
        best = best_deal(listing, cfg) if listing.price > 0 else None
        if best is None:
            seen.delete_deal(d["key"])
            removed += 1
        else:
            seen.update_score(d["key"], *best)
            if d.get("ai"):  # l'avis IA déjà donné reste valable : on le réapplique
                import json
                seen.apply_ai(d["key"], json.loads(d["ai"]), cfg)
            updated += 1
    return updated, removed


def best_deal(listing: Listing, cfg: Config):
    """Meilleure règle correspondante : (affaire, est_bonne), ou None si aucune règle."""
    rules = {r.name: r for r in cfg.rules}
    scored = [(d, is_good(d, rules[d.rule_name], cfg)) for d in find_deals(listing, cfg, only_good=False)]
    if not scored:
        return None
    # La règle la plus précise (le plus de mots-clés trouvés) puis la plus prudente : un jeu Game Boy
    # ne doit pas être coté comme une console, ni un jeu N64 comme une Nintendo 64.
    t = normalize(listing.title)

    def specificity(rule):
        words = [x for w in rule.all for x in (w if isinstance(w, list) else [w])] + list(rule.any)
        return sum(1 for w in words if contains(t, w))

    return max(scored, key=lambda x: (x[1], specificity(rules[x[0].rule_name]), -x[0].est_resale))


def loop(cfg: Config, seen: Seen, every: Optional[int], config_path: Optional[str] = None) -> int:
    while True:
        status = seen.status
        if config_path:
            try:
                if reload_if_changed(cfg, config_path):
                    n, gone = rescore(cfg, seen)
                    print(f"[config] config.yaml rechargé : {n} affaire(s) réévaluée(s), {gone} retirée(s)")
                status.update(config_error=None)
            except Exception as e:
                status.update(config_error=f"Erreur dans config.yaml : {e}")
        n = run_once(cfg, seen)
        if n:
            print(f"[{time.strftime('%H:%M:%S')}] {n} bonne(s) affaire(s) trouvée(s)")
        if not every:
            return 1 if status.get("error") else 0
        WAKE.wait(every)
        WAKE.clear()

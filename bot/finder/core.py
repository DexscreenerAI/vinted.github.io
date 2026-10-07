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
        listing = Listing(title=title, price=price, url=str(it.get("url") or "")[:500],
                          image=str(it.get("image") or "")[:500], location=str(it.get("location") or "")[:100],
                          source=source)
        if source == "vinted":
            listing.buy_cost = round(price + VINTED_FEE_FIXED + VINTED_FEE_RATE * price + VINTED_SHIPPING, 2)
        best = best_deal(listing, cfg)
        if best is None:
            continue
        deal, good = best
        if seen.add(listing.key, listing.title, listing.price):
            seen.save_deal(deal, good)
            if good:
                try:
                    send(deal)
                except Exception as e:  # une notification ratée ne doit pas bloquer l'analyse
                    print(f"[notification] {e}", file=sys.stderr)
        results.append({"title": title, "price": price, "url": listing.url, "image": listing.image,
                        "rule": deal.rule_name, "category": deal.category, "ratio": deal.ratio,
                        "net_profit": deal.net_profit, "buy_cost": deal.buy_cost, "est_resale": deal.est_resale,
                        "good": good})
    results.sort(key=lambda r: (r["good"], r["ratio"]), reverse=True)
    return {"received": len(items), "matched": len(results), "good": sum(r["good"] for r in results),
            "source": source, "deals": results}


def process(listings, cfg: Config, seen: Seen) -> int:
    """Évalue et enregistre les annonces jamais vues ; renvoie le nombre de bonnes affaires."""
    found = 0
    for listing in listings:
        if not seen.add(listing.key, listing.title, listing.price):
            continue
        best = best_deal(listing, cfg)
        if best is None:
            continue
        deal, good = best
        seen.save_deal(deal, good)
        if good:
            send(deal)
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


def best_deal(listing: Listing, cfg: Config):
    """Meilleure règle correspondante : (affaire, est_bonne), ou None si aucune règle."""
    rules = {r.name: r for r in cfg.rules}
    scored = [(d, is_good(d, rules[d.rule_name], cfg)) for d in find_deals(listing, cfg, only_good=False)]
    if not scored:
        return None
    return max(scored, key=lambda x: (x[1], x[0].ratio))


def loop(cfg: Config, seen: Seen, every: Optional[int], config_path: Optional[str] = None) -> int:
    while True:
        status = seen.status
        if config_path:
            try:
                if reload_if_changed(cfg, config_path):
                    print("[config] config.yaml rechargé")
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

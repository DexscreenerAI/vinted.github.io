"""Boucle principale : lecture des alertes, évaluation, enregistrement."""

import os
import sys
import time
from pathlib import Path
from typing import Optional

from .email_source import fetch_alerts
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


def run_once(cfg: Config, seen: Seen) -> int:
    found = 0
    for subject, listings in fetch_alerts():
        print(f"[alerte] {subject} : {len(listings)} annonce(s)")
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
        if not os.environ.get("IMAP_HOST"):
            status.update(error="Boîte mail non configurée : cliquez sur « Réglages »")
            if not every:
                print(status["error"], file=sys.stderr)
                return 1
        else:
            try:
                n = run_once(cfg, seen)
                status.update(last_check=time.strftime("%H:%M"), error=None)
                print(f"[{time.strftime('%H:%M:%S')}] {n} bonne(s) affaire(s) trouvée(s)")
            except Exception as e:  # on ne veut pas que le bot s'arrête sur une erreur réseau
                status.update(error=f"Lecture des emails impossible : {e}")
                print(f"[erreur] {e}", file=sys.stderr)
                if not every:
                    return 1
        if not every:
            return 0
        time.sleep(every)

"""Détecteur de bonnes affaires Leboncoin → Vinted.

  python -m finder run                 # lit les nouvelles alertes email une fois (pour cron)
  python -m finder run --loop 300      # tourne en continu, toutes les 5 min
  python -m finder web                 # page web des résultats sur http://localhost:8000
  python -m finder parse alerte.eml    # teste l'extraction sur un email sauvegardé
  python -m finder check "Veste Carhartt Detroit" 30 [--main-propre]
"""

import argparse
import email
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import yaml

from .email_source import fetch_alerts, html_part, parse_alert_html
from .models import Listing
from .notify import send
from .scoring import Config, find_deals, is_good
from .store import Seen


def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.from_dict(yaml.safe_load(f))


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


def loop(cfg: Config, seen: Seen, every: Optional[int]) -> int:
    while True:
        try:
            n = run_once(cfg, seen)
            print(f"[{time.strftime('%H:%M:%S')}] {n} bonne(s) affaire(s) trouvée(s)")
        except Exception as e:  # on ne veut pas que le bot s'arrête sur une erreur réseau
            print(f"[erreur] {e}", file=sys.stderr)
            if not every:
                return 1
        if not every:
            return 0
        time.sleep(every)


def read_listings(path: str):
    raw = Path(path).read_bytes()
    if path.endswith(".eml"):
        return parse_alert_html(html_part(email.message_from_bytes(raw)))
    return parse_alert_html(raw.decode("utf-8", errors="replace"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="finder", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="traiter les alertes email Leboncoin")
    p_run.add_argument("--loop", type=int, metavar="SECONDES", help="répéter toutes les N secondes")
    p_run.add_argument("--db", default="finder.db")

    p_web = sub.add_parser("web", help="page web des résultats + lecture des alertes en continu")
    p_web.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    p_web.add_argument("--loop", type=int, default=120, metavar="SECONDES", help="intervalle de lecture des emails")
    p_web.add_argument("--db", default="finder.db")

    p_parse = sub.add_parser("parse", help="tester l'extraction d'un email (.eml ou .html)")
    p_parse.add_argument("file")

    p_check = sub.add_parser("check", help="évaluer une annonce à la main")
    p_check.add_argument("title")
    p_check.add_argument("price", type=float)
    p_check.add_argument("--main-propre", action="store_true", help="remise en main propre (pas de frais/port)")

    args = ap.parse_args(argv)
    load_env()
    cfg = load_config(args.config)

    if args.cmd == "parse":
        for listing in read_listings(args.file):
            deals = find_deals(listing, cfg, only_good=False)
            verdict = f"x{deals[0].ratio:.1f} ({deals[0].rule_name})" if deals else "aucune règle"
            print(f"{listing.price:>7.0f} € | {listing.title[:60]:<60} | {verdict}")
        return 0

    if args.cmd == "check":
        if args.main_propre:
            cfg.hand_delivery = True
        deals = find_deals(Listing(title=args.title, price=args.price), cfg, only_good=False)
        if not deals:
            print("Aucune règle ne correspond à ce titre.")
            return 1
        for d in deals:
            rule = next(r for r in cfg.rules if r.name == d.rule_name)
            print(("✅ BONNE AFFAIRE" if is_good(d, rule, cfg) else "❌ pas assez rentable"))
            send(d)
        return 0

    seen = Seen(args.db)
    if args.cmd == "web":
        from .web import serve
        if os.environ.get("IMAP_HOST"):
            threading.Thread(target=loop, args=(cfg, seen, args.loop), daemon=True).start()
        else:
            print("IMAP_HOST non défini : la page s'affiche mais aucune alerte email n'est lue.")
        serve(cfg, seen, args.port)
        return 0
    return loop(cfg, seen, args.loop)


if __name__ == "__main__":
    sys.exit(main())

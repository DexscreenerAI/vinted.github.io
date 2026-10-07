"""Détecteur de bonnes affaires Leboncoin → Vinted.

  python -m finder                     # lance l'application (page web ouverte automatiquement)
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
from pathlib import Path

from .core import load_config, load_env, loop
from .email_source import html_part, parse_alert_html
from .models import Listing
from .notify import send
from .scoring import find_deals, is_good
from .store import Seen


def read_listings(path: str):
    raw = Path(path).read_bytes()
    if path.endswith(".eml"):
        return parse_alert_html(html_part(email.message_from_bytes(raw)))
    return parse_alert_html(raw.decode("utf-8", errors="replace"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="finder", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd")

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
    if args.cmd is None:
        from .app import launch
        return launch()
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
        threading.Thread(target=loop, args=(cfg, seen, args.loop, args.config), daemon=True).start()
        serve(cfg, seen, args.port, env_path=".env", config_path=args.config)
        return 0
    return loop(cfg, seen, args.loop)


if __name__ == "__main__":
    sys.exit(main())

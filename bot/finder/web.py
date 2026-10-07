"""Page web des résultats (serveur HTTP de la bibliothèque standard, sans dépendance)."""

import base64
import hmac
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .email_source import test_login
from .models import Listing
from .sales import set_ref_price
from .scoring import Config, find_deals, is_good, reload_if_changed
from .store import Seen

PAGE = Path(__file__).with_name("page.html")
# Extension Chrome (dossier bot/extension, embarqué dans le .exe)
EXT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "extension"


def extension_zip() -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(EXT_DIR.iterdir()):
            if f.is_file():
                z.write(f, f"chasseur-extension/{f.name}")
    return buf.getvalue()
MIN_SALES_FOR_REF = 3  # ventes nécessaires avant de proposer « Mettre à jour la cote »

# Réglages modifiables depuis la page (enregistrés dans .env)
SETTINGS = ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
SECRETS = ("IMAP_PASSWORD", "EBAY_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN")


def save_env(path: str, values: dict) -> None:
    """Met à jour les clés données dans le fichier .env en gardant les autres lignes."""
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in remaining.items()]
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def open_file(path: str) -> None:
    """Ouvre un fichier dans l'éditeur par défaut (Bloc-notes sous Windows)."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def make_server(cfg: Config, seen: Seen, port: int, env_path: str = ".env",
                config_path: Optional[str] = None, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    password = os.environ.get("WEB_PASSWORD", "")          # protège toute la page (facultatif)
    admin_password = os.environ.get("ADMIN_PASSWORD", "")  # protège seulement les réglages si la page est publique

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _local(self) -> bool:
            return self.client_address[0] in ("127.0.0.1", "::1")

        def _given_password(self) -> str:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return ""
            try:
                return base64.b64decode(header[6:]).decode().partition(":")[2]
            except ValueError:
                return ""

        def _authorized(self) -> bool:
            return not password or hmac.compare_digest(self._given_password(), password)

        def _admin(self) -> bool:
            """Réglages : depuis le PC du bot, ou avec un mot de passe. Jamais en accès public libre."""
            if self._local():
                return True
            given = self._given_password()
            return any(p and hmac.compare_digest(given, p) for p in (password, admin_password))

        def _admin_guard(self) -> bool:
            if self._admin():
                return True
            self.send_response(401)
            if admin_password:
                self.send_header("WWW-Authenticate", 'Basic realm="reglages"')
            self.send_header("Content-Type", "application/json; charset=utf-8")
            body = json.dumps({"ok": False, "error": "Action réservée au PC où tourne le bot (ou avec le mot de passe)."},
                              ensure_ascii=False).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _guard(self) -> bool:
            if self._authorized():
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="finder"')
            self.end_headers()
            return False

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def _sales_payload(self) -> dict:
            if config_path:
                try:
                    reload_if_changed(cfg, config_path)
                except Exception:
                    pass  # erreur de config déjà signalée par la boucle principale
            c = cfg.costs
            refs = {r.name: r.ref_price for r in cfg.rules}
            return {
                "sales": seen.sales(c.tax_rate, c.packaging),
                "stats": seen.sale_stats(c.tax_rate, c.packaging, refs),
                "rules": [{"name": r.name, "category": r.category, "lot": r.lot} for r in cfg.rules],
                "costs": {"tax_rate": c.tax_rate, "packaging": c.packaging},
                "min_sales": MIN_SALES_FOR_REF,
                "can_edit": self._admin(),
                "can_update_ref": bool(config_path),
            }

        def _record_sale(self, data: dict) -> None:
            try:
                sale_price = float(data["sale_price"])
                fees = float(data.get("fees") or 0)
                pieces = int(data["pieces"]) if data.get("pieces") else None
            except (KeyError, ValueError, TypeError):
                self._json({"ok": False, "error": "prix de vente requis"}, 400)
                return
            if sale_price < 0 or fees < 0 or (pieces is not None and pieces < 1):
                self._json({"ok": False, "error": "valeurs invalides"}, 400)
                return
            if data.get("key"):  # vente d'une affaire achetée via la page
                sale_id = seen.sell_deal(str(data["key"]), sale_price, fees, pieces)
                if sale_id is None:
                    self._json({"ok": False, "error": "affaire introuvable"}, 404)
                    return
                self._json({"ok": True, "id": sale_id})
                return
            rules = {r.name: r for r in cfg.rules}
            title = str(data.get("title", "")).strip()
            rule = rules.get(str(data.get("rule", "")))
            try:
                buy_price = float(data["buy_price"])
            except (KeyError, ValueError, TypeError):
                buy_price = -1
            if not title or rule is None or buy_price < 0:
                self._json({"ok": False, "error": "titre, règle et prix d'achat requis"}, 400)
                return
            sale_id = seen.add_sale(rule.name, title, buy_price, sale_price, fees, rule.category, pieces=pieces or 1)
            self._json({"ok": True, "id": sale_id})

        def _update_ref_price(self, data: dict) -> None:
            rule = str(data.get("rule", ""))
            if not config_path:
                self._json({"ok": False, "error": "config.yaml inconnu"}, 400)
                return
            found = seen.rule_median(rule)
            if not found or found[0] < MIN_SALES_FOR_REF:
                self._json({"ok": False, "error": f"il faut au moins {MIN_SALES_FOR_REF} ventes"}, 400)
                return
            value = round(found[1])  # médiane arrondie à l'euro, calculée ici (pas envoyée par la page)
            try:
                old = set_ref_price(config_path, rule, value)
                reload_if_changed(cfg, config_path)
            except (OSError, ValueError) as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            self._json({"ok": True, "rule": rule, "old": old, "ref_price": value})

        def do_GET(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/deals":
                view = parse_qs(url.query).get("view", ["bonnes"])[0]
                from .core import SOURCES
                status = dict(seen.status, configured=[name for name, ok, _ in SOURCES if ok()])
                self._json({"stats": seen.stats(), "deals": seen.deals(view), "status": status})
            elif url.path == "/api/ping":
                self._json({"ok": True, "app": "chasseur"})
            elif url.path == "/extension.zip":
                body = extension_zip()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="chasseur-extension.zip"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/rules":
                from .links import rules_with_links
                if config_path:
                    reload_if_changed(cfg, config_path)
                self._json(rules_with_links(cfg.rules))
            elif url.path == "/api/sales":
                self._json(self._sales_payload())
            elif url.path == "/api/settings":
                if not self._admin_guard():
                    return
                data = {k: ("" if k in SECRETS else os.environ.get(k, "")) for k in SETTINGS}
                data.update({f"{k}_set": bool(os.environ.get(k)) for k in SECRETS})
                data["can_open_config"] = bool(config_path) and self._local()
                data["has_telegram"] = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
                self._json(data)
            else:
                self._send(404, b"introuvable", "text/plain")

        def do_POST(self):
            if not self._guard():
                return
            data = self._body()
            if self.path == "/api/status":
                ok = seen.set_status(str(data.get("key", "")), str(data.get("status", "")))
                self._json({"ok": ok}, 200 if ok else 400)
            elif self.path == "/api/import":
                if not self._admin_guard():
                    return
                from .core import import_listings
                if config_path:
                    reload_if_changed(cfg, config_path)
                source = str(data.get("source") or "")
                if source not in ("leboncoin", "vinted", "ebay") or not isinstance(data.get("listings"), list):
                    self._json({"error": "page non reconnue"}, 400)
                    return
                self._json(import_listings(data["listings"], source, cfg, seen))
            elif self.path == "/api/check-now":
                from .core import WAKE
                WAKE.set()
                self._json({"ok": True})
            elif self.path == "/api/check":
                try:
                    listing = Listing(title=str(data["title"]), price=float(data["price"]))
                except (KeyError, ValueError, TypeError):
                    self._json({"error": "titre et prix requis"}, 400)
                    return
                if config_path:
                    reload_if_changed(cfg, config_path)
                rules = {r.name: r for r in cfg.rules}
                deals = find_deals(listing, cfg, only_good=False, hand_delivery=bool(data.get("hand")))
                self._json([
                    {"rule": d.rule_name, "pieces": d.pieces, "buy_cost": d.buy_cost,
                     "est_resale": d.est_resale, "net_profit": d.net_profit, "ratio": d.ratio,
                     "notes": d.notes, "good": is_good(d, rules[d.rule_name], cfg)}
                    for d in deals
                ])
            elif self.path in ("/api/settings", "/api/test-email", "/api/test-ebay", "/api/open-config", "/api/sales",
                               "/api/sales/delete", "/api/ref-price") and not self._admin_guard():
                return
            elif self.path == "/api/sales":
                self._record_sale(data)
            elif self.path == "/api/sales/delete":
                try:
                    ok = seen.delete_sale(int(data.get("id")))
                except (TypeError, ValueError):
                    ok = False
                self._json({"ok": ok}, 200 if ok else 404)
            elif self.path == "/api/ref-price":
                self._update_ref_price(data)
            elif self.path == "/api/settings":
                values = {}
                for k in SETTINGS:
                    v = str(data.get(k, "")).strip().replace("\n", "")
                    if k in SECRETS and not v:
                        continue  # champ laissé vide = on garde l'ancienne valeur
                    values[k] = v
                save_env(env_path, values)
                os.environ.update(values)
                seen.status.update(error=None)
                self._json({"ok": True})
            elif self.path == "/api/test-ebay":
                from . import ebay_source
                saved = {k: os.environ.get(k) for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET")}
                for k in saved:  # clés du formulaire si fournies, sinon celles enregistrées
                    if str(data.get(k) or "").strip():
                        os.environ[k] = str(data[k]).strip()
                try:
                    rule = next((r for r in cfg.rules if r.name.startswith("Carhartt Detroit")), cfg.rules[0])
                    query = ebay_source.rule_queries(rule)[0]
                    res = ebay_source._search(ebay_source.build_params(rule, query, "fixed"))
                    self._json({"ok": True, "query": query, "count": int(res.get("total", 0))})
                except Exception as e:
                    self._json({"ok": False, "error": str(e) or e.__class__.__name__})
                finally:
                    for k, v in saved.items():
                        if v is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = v
            elif self.path == "/api/test-email":
                host = str(data.get("IMAP_HOST") or os.environ.get("IMAP_HOST", "")).strip()
                user = str(data.get("IMAP_USER") or os.environ.get("IMAP_USER", "")).strip()
                pwd = str(data.get("IMAP_PASSWORD") or os.environ.get("IMAP_PASSWORD", "")).strip()
                try:
                    n = test_login(host, user, pwd)
                    self._json({"ok": True, "count": n})
                except Exception as e:
                    self._json({"ok": False, "error": str(e) or e.__class__.__name__})
            elif self.path == "/api/open-config":
                if not (config_path and self._local()):
                    self._json({"ok": False}, 403)
                    return
                open_file(os.path.abspath(config_path))
                self._json({"ok": True})
            else:
                self._send(404, b"introuvable", "text/plain")

    return ThreadingHTTPServer((host, port), Handler)


def serve(cfg: Config, seen: Seen, port: int, env_path: str = ".env", config_path: Optional[str] = None,
          host: str = "0.0.0.0") -> None:
    server = make_server(cfg, seen, port, env_path, config_path, host)
    print(f"Page des résultats : http://localhost:{port}"
          + ("" if os.environ.get("WEB_PASSWORD") else "  (sans mot de passe)"))
    server.serve_forever()

"""Page web des résultats (serveur HTTP de la bibliothèque standard, sans dépendance)."""

import base64
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .models import Listing
from .scoring import Config, find_deals, is_good
from .store import Seen

PAGE = Path(__file__).with_name("page.html")


def serve(cfg: Config, seen: Seen, port: int) -> None:
    password = os.environ.get("WEB_PASSWORD", "")
    rules = {r.name: r for r in cfg.rules}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _authorized(self) -> bool:
            if not password:
                return True
            header = self.headers.get("Authorization", "")
            if header.startswith("Basic "):
                try:
                    _, _, given = base64.b64decode(header[6:]).decode().partition(":")
                except ValueError:
                    return False
                return hmac.compare_digest(given, password)
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

        def do_GET(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/deals":
                view = parse_qs(url.query).get("view", ["bonnes"])[0]
                self._json({"stats": seen.stats(), "deals": seen.deals(view)})
            else:
                self._send(404, b"introuvable", "text/plain")

        def do_POST(self):
            if not self._guard():
                return
            data = self._body()
            if self.path == "/api/status":
                ok = seen.set_status(str(data.get("key", "")), str(data.get("status", "")))
                self._json({"ok": ok}, 200 if ok else 400)
            elif self.path == "/api/check":
                try:
                    listing = Listing(title=str(data["title"]), price=float(data["price"]))
                except (KeyError, ValueError, TypeError):
                    self._json({"error": "titre et prix requis"}, 400)
                    return
                deals = find_deals(listing, cfg, only_good=False, hand_delivery=bool(data.get("hand")))
                self._json([
                    {"rule": d.rule_name, "pieces": d.pieces, "buy_cost": d.buy_cost,
                     "est_resale": d.est_resale, "net_profit": d.net_profit, "ratio": d.ratio,
                     "notes": d.notes, "good": is_good(d, rules[d.rule_name], cfg)}
                    for d in deals
                ])
            else:
                self._send(404, b"introuvable", "text/plain")

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Page des résultats : http://localhost:{port}" + ("" if password else "  (sans mot de passe)"))
    server.serve_forever()

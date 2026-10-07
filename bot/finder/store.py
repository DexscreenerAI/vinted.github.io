import json
import sqlite3
import threading
from typing import List, Optional

from .models import Deal

STATUSES = ("nouveau", "achete", "ignore")


class Seen:
    """Mémorise les annonces déjà traitées et les affaires trouvées (SQLite)."""

    def __init__(self, path: str = "finder.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.status = {"last_check": None, "error": None}  # état de la lecture des emails, affiché sur la page
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, title TEXT, price REAL,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS deals (
                key TEXT PRIMARY KEY, title TEXT, price REAL, url TEXT, image TEXT, location TEXT,
                rule TEXT, pieces INTEGER, buy_cost REAL, est_resale REAL, net_profit REAL,
                ratio REAL, good INTEGER, notes TEXT, status TEXT DEFAULT 'nouveau',
                found_at TEXT DEFAULT (datetime('now', 'localtime')));
            """
        )
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(deals)")}
        if "category" not in cols:  # base créée par une version précédente
            self.db.execute("ALTER TABLE deals ADD COLUMN category TEXT DEFAULT 'Autre'")
            self.db.commit()

    def add(self, key: str, title: str, price) -> bool:
        """True si l'annonce est nouvelle."""
        with self.lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO seen (key, title, price) VALUES (?, ?, ?)", (key, title, price)
            )
            self.db.commit()
        return cur.rowcount == 1

    def save_deal(self, deal: Deal, good: bool) -> None:
        l = deal.listing
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO deals (key, title, price, url, image, location, rule, pieces,"
                " buy_cost, est_resale, net_profit, ratio, good, notes, category)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (l.key, l.title, l.price, l.url, l.image, l.location, deal.rule_name, deal.pieces,
                 deal.buy_cost, deal.est_resale, deal.net_profit, deal.ratio, int(good),
                 json.dumps(deal.notes, ensure_ascii=False), deal.category),
            )
            self.db.commit()

    def deals(self, view: str = "bonnes", limit: int = 300) -> List[dict]:
        where = {
            "bonnes": "good = 1 AND status = 'nouveau'",
            "toutes": "status = 'nouveau'",
            "achete": "status = 'achete'",
            "ignore": "status = 'ignore'",
        }.get(view, "good = 1 AND status = 'nouveau'")
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM deals WHERE {where} ORDER BY found_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["notes"] = json.loads(d["notes"] or "[]")
            out.append(d)
        return out

    def stats(self) -> dict:
        with self.lock:
            r = self.db.execute(
                "SELECT"
                " SUM(good = 1 AND status = 'nouveau') AS bonnes,"
                " SUM(good = 1 AND status = 'nouveau' AND date(found_at) = date('now', 'localtime')) AS aujourdhui,"
                " SUM(status = 'achete') AS achetes,"
                " COALESCE(SUM(CASE WHEN status = 'achete' THEN net_profit END), 0) AS profit_achetes"
                " FROM deals"
            ).fetchone()
            seen = self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        d = {k: (r[k] or 0) for k in r.keys()}
        d["annonces_lues"] = seen
        return d

    def set_status(self, key: str, status: str) -> bool:
        if status not in STATUSES:
            return False
        with self.lock:
            cur = self.db.execute("UPDATE deals SET status = ? WHERE key = ?", (status, key))
            self.db.commit()
        return cur.rowcount == 1

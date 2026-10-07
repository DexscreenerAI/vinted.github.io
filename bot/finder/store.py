import json
import sqlite3
import statistics
import threading
from typing import Dict, List, Optional

from .models import Deal

STATUSES = ("nouveau", "achete", "vendu", "ignore")


class Seen:
    """Mémorise les annonces déjà traitées et les affaires trouvées (SQLite)."""

    def __init__(self, path: str = "finder.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        # état des sources (emails, eBay…), affiché sur la page
        self.status = {"last_check": None, "error": None, "sources": {}}
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, title TEXT, price REAL,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS deals (
                key TEXT PRIMARY KEY, title TEXT, price REAL, url TEXT, image TEXT, location TEXT,
                rule TEXT, pieces INTEGER, buy_cost REAL, est_resale REAL, net_profit REAL,
                ratio REAL, good INTEGER, notes TEXT, status TEXT DEFAULT 'nouveau',
                found_at TEXT DEFAULT (datetime('now', 'localtime')));
            CREATE TABLE IF NOT EXISTS emails (msgid TEXT PRIMARY KEY, site TEXT, subject TEXT,
                listings INTEGER, received_at TEXT DEFAULT (datetime('now', 'localtime')));
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT, deal_key TEXT, rule TEXT, category TEXT, title TEXT,
                buy_price REAL, sale_price REAL, fees REAL DEFAULT 0, pieces INTEGER DEFAULT 1,
                bought_at TEXT, sold_at TEXT DEFAULT (datetime('now', 'localtime')));
            """
        )
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(deals)")}
        for col, decl in (("category", "TEXT DEFAULT 'Autre'"), ("source", "TEXT DEFAULT 'leboncoin'"),
                          ("ends_at", "TEXT DEFAULT ''"), ("bought_at", "TEXT"), ("ai", "TEXT")):
            if col not in cols:  # base créée par une version précédente
                self.db.execute(f"ALTER TABLE deals ADD COLUMN {col} {decl}")
        self.db.commit()

    def email_done(self, msgid: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM emails WHERE msgid = ?", (msgid,)).fetchone() is not None

    def add_email(self, msgid: str, site: str, subject: str, listings: int) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO emails (msgid, site, subject, listings) VALUES (?, ?, ?, ?)",
                            (msgid, site, subject, listings))
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
                " buy_cost, est_resale, net_profit, ratio, good, notes, category, source, ends_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (l.key, l.title, l.price, l.url, l.image, l.location, deal.rule_name, deal.pieces,
                 deal.buy_cost, deal.est_resale, deal.net_profit, deal.ratio, int(good),
                 json.dumps(deal.notes, ensure_ascii=False), deal.category, l.source, l.ends_at),
            )
            self.db.commit()

    def deals(self, view: str = "bonnes", limit: int = 300) -> List[dict]:
        where = {
            "bonnes": "good = 1 AND status = 'nouveau'",
            "toutes": "status = 'nouveau'",
            "achete": "status = 'achete'",
            "vendu": "status = 'vendu'",
            "ignore": "status = 'ignore'",
        }.get(view, "good = 1 AND status = 'nouveau'")
        # pour les affaires vendues : la (dernière) vente enregistrée
        sale = ("(SELECT {} FROM sales s WHERE s.deal_key = deals.key ORDER BY s.id DESC LIMIT 1)")
        with self.lock:
            rows = self.db.execute(
                f"SELECT *, {sale.format('id')} AS sale_id, {sale.format('sale_price')} AS sale_price,"
                f" {sale.format('fees')} AS sale_fees, {sale.format('pieces')} AS sale_pieces"
                f" FROM deals WHERE {where} ORDER BY found_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["notes"] = json.loads(d["notes"] or "[]")
            d["ai"] = json.loads(d["ai"]) if d.get("ai") else None
            out.append(d)
        return out

    def good_since(self, since: str) -> int:
        """Bonnes affaires trouvées depuis `since` (« AAAA-MM-JJ HH:MM:SS », heure locale) : bilan de tournée."""
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM deals WHERE good = 1 AND found_at >= ?", (since,)).fetchone()[0]

    def stats(self) -> dict:
        with self.lock:
            r = self.db.execute(
                "SELECT"
                " SUM(good = 1 AND status = 'nouveau') AS bonnes,"
                " SUM(good = 1 AND status = 'nouveau' AND date(found_at) = date('now', 'localtime')) AS aujourdhui,"
                " SUM(status = 'achete') AS achetes,"
                " COALESCE(SUM(CASE WHEN status = 'achete' THEN net_profit END), 0) AS profit_achetes,"
                " COUNT(*) AS correspondances, SUM(status = 'nouveau') AS a_voir_toutes"
                " FROM deals"
            ).fetchone()
            seen = self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
            em = self.db.execute("SELECT COUNT(*), SUM(listings = 0), GROUP_CONCAT(DISTINCT CASE WHEN listings = 0"
                                 " THEN site END) FROM emails").fetchone()
        d = {k: (r[k] or 0) for k in r.keys()}
        d["annonces_lues"] = seen
        d["emails_recus"], d["emails_illisibles"], d["sites_illisibles"] = em[0] or 0, em[1] or 0, em[2] or ""
        return d

    def pending_deals(self) -> List[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM deals WHERE status = 'nouveau'")]

    def update_score(self, key: str, deal: Deal, good: bool) -> None:
        """Nouvelle évaluation d'une affaire (règles modifiées), sans toucher à son statut ni à l'avis IA."""
        with self.lock:
            self.db.execute(
                "UPDATE deals SET rule = ?, category = ?, pieces = ?, buy_cost = ?, est_resale = ?, net_profit = ?,"
                " ratio = ?, good = ?, notes = ? WHERE key = ?",
                (deal.rule_name, deal.category, deal.pieces, deal.buy_cost, deal.est_resale, deal.net_profit,
                 deal.ratio, int(good), json.dumps(deal.notes, ensure_ascii=False), key))
            self.db.commit()

    def delete_deal(self, key: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM deals WHERE key = ?", (key,))
            self.db.commit()

    def get_deal(self, key: str) -> Optional[dict]:
        with self.lock:
            r = self.db.execute("SELECT * FROM deals WHERE key = ?", (key,)).fetchone()
        return dict(r) if r else None

    def set_ai(self, key: str, result: dict) -> None:
        """Mémorise l'avis de Claude sur une affaire (évite de repayer une analyse)."""
        with self.lock:
            self.db.execute("UPDATE deals SET ai = ? WHERE key = ?", (json.dumps(result, ensure_ascii=False), key))
            self.db.commit()

    def set_status(self, key: str, status: str) -> bool:
        if status not in STATUSES:
            return False
        with self.lock:
            # date d'achat : posée au passage en « achete », effacée si l'affaire est remise ou ignorée
            cur = self.db.execute(
                "UPDATE deals SET status = ?, bought_at = CASE"
                " WHEN ? = 'achete' AND status != 'achete' THEN datetime('now', 'localtime')"
                " WHEN ? IN ('nouveau', 'ignore') THEN NULL ELSE bought_at END WHERE key = ?",
                (status, status, status, key))
            self.db.commit()
        return cur.rowcount == 1

    # ---------- Mes ventes ----------

    def add_sale(self, rule: str, title: str, buy_price: float, sale_price: float, fees: float = 0.0,
                 category: Optional[str] = None, deal_key: Optional[str] = None, pieces: int = 1,
                 bought_at: Optional[str] = None, sold_at: Optional[str] = None) -> int:
        """Enregistre une vente réelle ; renvoie son identifiant."""
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO sales (deal_key, rule, category, title, buy_price, sale_price, fees, pieces,"
                " bought_at, sold_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " COALESCE(?, datetime('now', 'localtime')))",
                (deal_key, rule, category or "Autre", title, float(buy_price), float(sale_price),
                 float(fees or 0), max(1, int(pieces or 1)), bought_at, sold_at),
            )
            self.db.commit()
        return cur.lastrowid

    def sell_deal(self, key: str, sale_price: float, fees: float = 0.0, pieces: Optional[int] = None) -> Optional[int]:
        """Vente d'une affaire de la page : enregistre la vente et passe l'affaire en « vendu »."""
        with self.lock:
            d = self.db.execute("SELECT * FROM deals WHERE key = ?", (key,)).fetchone()
        if d is None:
            return None
        sale_id = self.add_sale(d["rule"], d["title"], d["buy_cost"] or d["price"] or 0, sale_price, fees,
                                d["category"], key, pieces or d["pieces"] or 1, d["bought_at"])
        with self.lock:
            self.db.execute("UPDATE deals SET status = 'vendu' WHERE key = ?", (key,))
            self.db.commit()
        return sale_id

    def delete_sale(self, sale_id: int) -> bool:
        """Supprime une vente ; l'affaire correspondante revient dans « Achetées »."""
        with self.lock:
            row = self.db.execute("SELECT deal_key FROM sales WHERE id = ?", (sale_id,)).fetchone()
            if row is None:
                return False
            self.db.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
            if row["deal_key"]:
                self.db.execute(
                    "UPDATE deals SET status = 'achete' WHERE key = ? AND status = 'vendu'"
                    " AND NOT EXISTS (SELECT 1 FROM sales WHERE deal_key = ?)", (row["deal_key"], row["deal_key"]))
            self.db.commit()
        return True

    @staticmethod
    def real_profit(sale: dict, tax_rate: float, packaging: float) -> float:
        """Bénéfice réel : prix de vente − achat − frais − cotisations/impôt − emballage."""
        return round(sale["sale_price"] - sale["buy_price"] - (sale["fees"] or 0)
                     - tax_rate * sale["sale_price"] - packaging * (sale["pieces"] or 1), 2)

    def sales(self, tax_rate: float = 0.0, packaging: float = 0.0, limit: int = 500) -> List[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT *, julianday(sold_at) - julianday(bought_at) AS days FROM sales"
                " ORDER BY sold_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            s = dict(r)
            s["profit"] = self.real_profit(s, tax_rate, packaging)
            out.append(s)
        return out

    def sale_stats(self, tax_rate: float = 0.0, packaging: float = 0.0,
                   ref_prices: Optional[Dict[str, float]] = None) -> dict:
        """Totaux réels et, par règle : nombre de ventes, prix de vente médian (par pièce), cote actuelle."""
        ref_prices = ref_prices or {}
        sales = self.sales(tax_rate, packaging, limit=-1)
        total = round(sum(s["profit"] for s in sales), 2)
        days = [s["days"] for s in sales if s["days"] is not None and s["days"] >= 0]
        by_rule: Dict[str, List[dict]] = {}
        for s in sales:
            by_rule.setdefault(s["rule"] or "", []).append(s)
        rules = []
        for name, group in by_rule.items():
            median = round(statistics.median(s["sale_price"] / (s["pieces"] or 1) for s in group), 2)
            ref = ref_prices.get(name)
            rules.append({
                "rule": name, "category": group[0]["category"] or "Autre", "count": len(group),
                "median_price": median, "ref_price": ref,
                "diff": None if ref is None else round(median - ref, 2),
                "profit": round(sum(s["profit"] for s in group), 2),
            })
        rules.sort(key=lambda r: (-r["count"], r["rule"]))
        return {
            "total_profit": total, "count": len(sales),
            "avg_profit": round(total / len(sales), 2) if sales else None,
            "avg_days": round(sum(days) / len(days), 1) if days else None,
            "rules": rules,
        }

    def rule_median(self, rule: str) -> Optional[tuple]:
        """(nombre de ventes, prix médian par pièce) pour une règle, ou None sans vente."""
        with self.lock:
            rows = self.db.execute("SELECT sale_price, pieces FROM sales WHERE rule = ?", (rule,)).fetchall()
        if not rows:
            return None
        return len(rows), statistics.median(r["sale_price"] / (r["pieces"] or 1) for r in rows)

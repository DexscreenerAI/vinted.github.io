import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import yaml

from finder.models import Listing
from finder.sales import set_ref_price
from finder.scoring import Config, find_deals, reload_if_changed
from finder.store import Seen

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.yaml"
CFG = Config.from_dict(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")))


def saved_deal(seen: Seen, title="Veste Carhartt Detroit", price=20, ad="1234567890"):
    deal = find_deals(Listing(title, price, f"https://www.leboncoin.fr/ad/vetements/{ad}"), CFG)[0]
    seen.add(deal.listing.key, title, price)
    seen.save_deal(deal, True)
    return deal


class SalesStoreTest(unittest.TestCase):
    def test_profit_and_median(self):
        seen = Seen(":memory:")
        for buy, sale in ((20, 80), (25, 90), (30, 100), (10, 60)):
            seen.add_sale("Carhartt Detroit jacket", "Veste", buy, sale, fees=2, category="Vêtements")
        seen.add_sale("Levi's 501 Made in USA", "Jean", 10, 50)
        st = seen.sale_stats(tax_rate=0.1, packaging=0.5,
                             ref_prices={"Carhartt Detroit jacket": 85, "Levi's 501 Made in USA": 45})
        self.assertEqual(st["count"], 5)
        # Detroit : (330 ventes − 85 achats − 8 frais − 33 impôt − 2 emballage) ; 501 : 50 − 10 − 5 − 0,5
        self.assertAlmostEqual(st["total_profit"], 202 + 34.5)
        self.assertAlmostEqual(st["avg_profit"], round(236.5 / 5, 2))
        detroit = next(r for r in st["rules"] if r["rule"] == "Carhartt Detroit jacket")
        self.assertEqual(detroit["count"], 4)
        self.assertEqual(detroit["median_price"], 85)      # médiane de 60, 80, 90, 100
        self.assertEqual(detroit["ref_price"], 85)
        self.assertEqual(detroit["diff"], 0)
        self.assertEqual(detroit["category"], "Vêtements")
        self.assertEqual(st["rules"][0]["rule"], "Carhartt Detroit jacket")  # plus de ventes en premier
        self.assertEqual(seen.rule_median("Carhartt Detroit jacket"), (4, 85))
        self.assertIsNone(seen.rule_median("inconnue"))

    def test_lot_median_is_per_piece(self):
        seen = Seen(":memory:")
        seen.add_sale("Lot", "Lot de 10 polos", 40, 150, pieces=6)
        st = seen.sale_stats()
        self.assertEqual(st["rules"][0]["median_price"], 25)
        self.assertIsNone(st["rules"][0]["ref_price"])   # règle absente de config.yaml
        self.assertIsNone(st["rules"][0]["diff"])

    def test_empty(self):
        st = Seen(":memory:").sale_stats()
        self.assertEqual((st["count"], st["total_profit"], st["avg_profit"], st["avg_days"]), (0, 0, None, None))

    def test_status_flow(self):
        seen = Seen(":memory:")
        deal = saved_deal(seen)
        key = deal.listing.key
        self.assertIsNone(seen.deals("bonnes")[0]["bought_at"])
        self.assertTrue(seen.set_status(key, "achete"))
        bought = seen.deals("achete")[0]
        self.assertTrue(bought["bought_at"])

        sale_id = seen.sell_deal(key, 90, fees=1.5)
        self.assertEqual(seen.deals("achete"), [])
        sold = seen.deals("vendu")
        self.assertEqual(len(sold), 1)
        self.assertEqual((sold[0]["sale_id"], sold[0]["sale_price"], sold[0]["sale_fees"]), (sale_id, 90, 1.5))
        sale = seen.sales()[0]
        self.assertEqual(sale["buy_price"], deal.buy_cost)   # coût d'achat réel (frais + port compris)
        self.assertEqual(sale["deal_key"], key)
        self.assertEqual(sale["bought_at"], bought["bought_at"])
        self.assertAlmostEqual(sale["days"], 0, places=2)
        self.assertEqual(seen.sale_stats()["avg_days"], 0)
        self.assertIsNone(seen.sell_deal("inconnue", 10))

        # annuler la vente : l'affaire revient dans « Achetées »
        self.assertTrue(seen.delete_sale(sale_id))
        self.assertFalse(seen.delete_sale(sale_id))
        self.assertEqual(len(seen.deals("achete")), 1)
        self.assertEqual(seen.sales(), [])

        # remettre l'affaire efface la date d'achat
        self.assertTrue(seen.set_status(key, "nouveau"))
        self.assertIsNone(seen.deals("bonnes")[0]["bought_at"])
        self.assertIn("vendu", __import__("finder.store", fromlist=["STATUSES"]).STATUSES)

    def test_average_resale_delay(self):
        seen = Seen(":memory:")
        seen.add_sale("R", "a", 10, 30, bought_at="2026-10-01 10:00:00", sold_at="2026-10-05 10:00:00")
        seen.add_sale("R", "b", 10, 30, bought_at="2026-10-01 10:00:00", sold_at="2026-10-11 10:00:00")
        seen.add_sale("R", "c", 10, 30)  # vente manuelle sans date d'achat : ignorée pour le délai
        self.assertEqual(seen.sale_stats()["avg_days"], 7)

    def test_migration_adds_bought_at(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "old.db")
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE deals (key TEXT PRIMARY KEY, title TEXT, price REAL, url TEXT, image TEXT,"
                       " location TEXT, rule TEXT, pieces INTEGER, buy_cost REAL, est_resale REAL, net_profit REAL,"
                       " ratio REAL, good INTEGER, notes TEXT, status TEXT DEFAULT 'nouveau', found_at TEXT)")
            db.commit()
            db.close()
            seen = Seen(path)
            cols = {r[1] for r in seen.db.execute("PRAGMA table_info(deals)")}
            self.assertIn("bought_at", cols)
            seen.db.close()


class RefPriceRewriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "config.yaml"
        shutil.copy(EXAMPLE, self.path)
        self.original = self.path.read_text(encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def changed_lines(self):
        before = self.original.splitlines()
        after = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(before), len(after))
        return [(a, b) for a, b in zip(before, after) if a != b]

    def refs(self):
        cfg = Config.from_dict(yaml.safe_load(self.path.read_text(encoding="utf-8")))
        return {r.name: r.ref_price for r in cfg.rules}

    def test_apostrophe_rule(self):
        old = set_ref_price(str(self.path), "Levi's 501 Made in USA", 52)
        self.assertEqual(old, 45)
        self.assertEqual(self.changed_lines(), [("    ref_price: 45", "    ref_price: 52")])
        refs, before = self.refs(), {r.name: r.ref_price for r in CFG.rules}
        self.assertEqual(refs["Levi's 501 Made in USA"], 52)
        before["Levi's 501 Made in USA"] = 52
        self.assertEqual(refs, before)   # aucune autre règle touchée

    def test_special_characters_and_trailing_comment(self):
        name = "Pokémon Rouge/Bleu/Jaune (Game Boy)"   # la ligne « - name: » a un commentaire en fin
        old = CFG.rules[[r.name for r in CFG.rules].index(name)].ref_price
        set_ref_price(str(self.path), name, 27)
        changes = self.changed_lines()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0], (f"    ref_price: {old:g}", "    ref_price: 27"))
        self.assertEqual(self.refs()[name], 27)
        # les commentaires sont conservés
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# pile morte = -30 à -50 %", text)
        self.assertIn("# Règle d'or : jamais une marque seule", text)

    def test_prefix_names_do_not_collide(self):
        # « Carhartt Detroit jacket » ne doit pas toucher une règle dont le nom commence pareil
        self.path.write_text(
            "# mes règles\nrules:\n"
            "  - name: Carhartt Detroit jacket vintage\n    ref_price: 120   # rare\n"
            "  - name: 'Carhartt Detroit jacket'   # la bonne\n    category: Vêtements\n"
            "    ref_price: 85     # prudent\n    max_buy: 34\n"
            "  - name: Autre\n    ref_price: 10\n", encoding="utf-8")
        self.original = self.path.read_text(encoding="utf-8")
        set_ref_price(str(self.path), "Carhartt Detroit jacket", 92.0)
        self.assertEqual(self.changed_lines(), [("    ref_price: 85     # prudent", "    ref_price: 92     # prudent")])

    def test_rule_without_ref_price_stops_at_next_rule(self):
        self.path.write_text("rules:\n  - name: A\n    category: X\n  - name: B\n    ref_price: 10\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            set_ref_price(str(self.path), "A", 20)
        self.assertIn("ref_price: 10", self.path.read_text(encoding="utf-8"))

    def test_unknown_rule(self):
        with self.assertRaises(ValueError):
            set_ref_price(str(self.path), "N'existe pas", 20)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original)

    def test_windows_line_endings_kept(self):
        self.path.write_bytes(b"rules:\r\n  - name: A\r\n    ref_price: 10\r\n")
        set_ref_price(str(self.path), "A", 12)
        self.assertEqual(self.path.read_bytes(), b"rules:\r\n  - name: A\r\n    ref_price: 12\r\n")

    def test_hot_reload_picks_it_up(self):
        cfg = Config(rules=[])
        reload_if_changed(cfg, str(self.path))
        set_ref_price(str(self.path), "Carhartt Detroit jacket", 99)
        self.assertTrue(reload_if_changed(cfg, str(self.path)))
        self.assertEqual(next(r for r in cfg.rules if r.name == "Carhartt Detroit jacket").ref_price, 99)


class SalesApiTest(unittest.TestCase):
    """Parcours complet par l'API locale : ventes puis « Mettre à jour la cote »."""

    def setUp(self):
        from finder.web import make_server
        self.dir = tempfile.TemporaryDirectory()
        self.config = Path(self.dir.name) / "config.yaml"
        shutil.copy(EXAMPLE, self.config)
        self.cfg = Config(rules=[])
        reload_if_changed(self.cfg, str(self.config))
        self.seen = Seen(":memory:")
        self.server = make_server(self.cfg, self.seen, 0, env_path=str(Path(self.dir.name) / ".env"),
                                  config_path=str(self.config), host="127.0.0.1")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.seen.db.close()
        self.dir.cleanup()

    def call(self, path, body=None):
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_flow(self):
        deal = saved_deal(self.seen)
        self.seen.set_status(deal.listing.key, "achete")
        self.assertEqual(self.call("/api/sales", {"key": deal.listing.key, "sale_price": 100})[0], 200)
        self.assertEqual(self.seen.deals("vendu")[0]["sale_price"], 100)
        # trop peu de ventes pour changer la cote
        code, res = self.call("/api/ref-price", {"rule": "Carhartt Detroit jacket"})
        self.assertEqual(code, 400)
        for price in (70, 110):
            code, _ = self.call("/api/sales", {"title": "Detroit", "rule": "Carhartt Detroit jacket",
                                                "buy_price": 20, "sale_price": price})
            self.assertEqual(code, 200)
        self.assertEqual(self.call("/api/sales", {"title": "x", "rule": "inconnue", "buy_price": 1, "sale_price": 2})[0], 400)
        code, data = self.call("/api/sales")
        self.assertEqual(data["stats"]["count"], 3)
        self.assertTrue(data["can_edit"])
        code, res = self.call("/api/ref-price", {"rule": "Carhartt Detroit jacket"})
        self.assertEqual((code, res["ref_price"], res["old"]), (200, 100, 85))
        self.assertEqual(next(r for r in self.cfg.rules if r.name == "Carhartt Detroit jacket").ref_price, 100)
        sale_id = data["sales"][0]["id"]
        self.assertEqual(self.call("/api/sales/delete", {"id": sale_id}), (200, {"ok": True}))
        self.assertEqual(self.call("/api/sales/delete", {"id": sale_id})[0], 404)


if __name__ == "__main__":
    unittest.main()

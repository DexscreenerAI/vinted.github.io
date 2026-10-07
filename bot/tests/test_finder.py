import unittest
from pathlib import Path

import yaml

from finder.email_source import parse_alert_html
from finder.models import Listing
from finder.scoring import Config, find_deals
from finder.store import Seen
from finder.text import lot_size, parse_price

ROOT = Path(__file__).resolve().parent.parent
CFG = Config.from_dict(yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8")))


class TextTest(unittest.TestCase):
    def test_parse_price(self):
        self.assertEqual(parse_price("25 €"), 25)
        self.assertEqual(parse_price("1 200 €"), 1200)
        self.assertEqual(parse_price("35,50 €"), 35.5)
        self.assertIsNone(parse_price("Lyon 69003"))

    def test_normalize_spellings(self):
        from finder.text import contains, normalize
        self.assertTrue(contains(normalize("Casio G-Shock DW5600"), "dw-5600"))
        self.assertTrue(contains(normalize("Veste ARC'TERYX Beta"), "arcteryx"))
        self.assertTrue(contains(normalize("Doc Martens 1460"), "dr martens"))
        self.assertTrue(contains(normalize("Console N64"), ["nintendo 64", "n64"]))

    def test_lot_size(self):
        self.assertEqual(lot_size("Lot de 12 polos Ralph Lauren"), 12)
        self.assertEqual(lot_size("15 pulls homme"), 15)
        self.assertIsNone(lot_size("Lot vêtements"))


class ParseTest(unittest.TestCase):
    def test_alert_email(self):
        html = (ROOT / "tests/fixtures/alerte_lbc.html").read_text(encoding="utf-8")
        listings = parse_alert_html(html)
        self.assertEqual([l.price for l in listings], [25, 1200, 15])
        first = listings[0]
        self.assertEqual(first.title, "Veste Carhartt Detroit vintage marron")
        self.assertEqual(first.key, "2876543210")
        self.assertEqual(first.image, "https://img.leboncoin.fr/1.jpg")
        self.assertEqual(first.location, "Lyon 69003")
        self.assertEqual(listings[1].title, "Lot de 12 polos Ralph Lauren homme")


class ScoringTest(unittest.TestCase):
    def test_good_deal(self):
        deals = find_deals(Listing("Veste Carhartt Detroit vintage", 20), CFG)
        self.assertEqual(deals[0].rule_name, "Carhartt Detroit jacket")
        self.assertGreaterEqual(deals[0].ratio, 2.5)

    def test_too_expensive(self):
        self.assertEqual(find_deals(Listing("Veste Carhartt Detroit", 60), CFG), [])

    def test_excluded_fake(self):
        self.assertEqual(find_deals(Listing("Veste style carhartt detroit", 15), CFG, only_good=False), [])

    def test_lot_uses_piece_count(self):
        deal = find_deals(Listing("Lot de 12 polos Ralph Lauren homme", 40), CFG)[0]
        self.assertEqual(deal.pieces, 8)  # 12 x 65 % vendables
        self.assertGreater(deal.net_profit, 40)

    def test_hand_delivery_has_no_fees(self):
        CFG.hand_delivery = True
        try:
            deal = find_deals(Listing("Veste Carhartt Detroit", 20), CFG)[0]
        finally:
            CFG.hand_delivery = False
        self.assertEqual(deal.buy_cost, 20)


class StoreTest(unittest.TestCase):
    def test_save_and_status(self):
        seen = Seen(":memory:")
        deal = find_deals(Listing("Veste Carhartt Detroit", 20, "https://www.leboncoin.fr/ad/vetements/1234567890"), CFG)[0]
        self.assertTrue(seen.add(deal.listing.key, deal.listing.title, 20))
        self.assertFalse(seen.add(deal.listing.key, deal.listing.title, 20))
        seen.save_deal(deal, True)
        self.assertEqual(len(seen.deals("bonnes")), 1)
        self.assertTrue(seen.set_status("1234567890", "achete"))
        self.assertEqual(seen.deals("bonnes"), [])
        self.assertEqual(seen.stats()["achetes"], 1)
        self.assertFalse(seen.set_status("1234567890", "nimporte"))


class EnvTest(unittest.TestCase):
    def test_save_env_keeps_other_lines(self):
        import tempfile
        from finder.web import save_env
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text("# commentaire\nWEB_PASSWORD=x\nIMAP_USER=old@a.fr\n", encoding="utf-8")
            save_env(str(path), {"IMAP_USER": "new@a.fr", "IMAP_HOST": "imap.a.fr"})
            self.assertEqual(path.read_text(encoding="utf-8"),
                             "# commentaire\nWEB_PASSWORD=x\nIMAP_USER=new@a.fr\nIMAP_HOST=imap.a.fr\n")


if __name__ == "__main__":
    unittest.main()

import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finder import email_source as es  # noqa: E402
from finder.__main__ import read_listings  # noqa: E402


def fixture(name: str) -> str:
    return (ROOT / "tests/fixtures" / name).read_text(encoding="utf-8")


def make_email(sender: str, html: str, subject: str = "Alerte") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg.set_content("version texte")
    msg.add_alternative(html, subtype="html")
    return msg


class OutilsTest(unittest.TestCase):
    def test_amount(self):
        self.assertEqual(es.amount("1.234,56 €"), 1234.56)
        self.assertEqual(es.amount("EUR 12,00"), 12.0)
        self.assertEqual(es.amount("12,00 EUR"), 12.0)
        self.assertEqual(es.amount("12 € VB"), 12.0)
        self.assertEqual(es.amount("1 200 €"), 1200.0)
        self.assertEqual(es.amount("1.200 €"), 1200.0)
        self.assertEqual(es.amount("Mise à prix : 20 €"), 20.0)
        self.assertIsNone(es.amount("VB"))
        self.assertIsNone(es.amount("Lot 112"))

    def test_unwrap_url(self):
        self.assertEqual(es.unwrap_url("https://rover.ebay.com/rover/0/0/0?mpre=https%3A%2F%2Fwww.ebay.fr%2Fitm%2F123456789012"),
                         "https://www.ebay.fr/itm/123456789012")
        self.assertEqual(es.unwrap_url("https://t.example.com/c?url=https%253A%252F%252Fwww.kleinanzeigen.de%252Fs-anzeige%252Fx%252F1-2-3"),
                         "https://www.kleinanzeigen.de/s-anzeige/x/1-2-3")
        self.assertEqual(es.unwrap_url("https://www.leboncoin.fr/ad/x/123"), "https://www.leboncoin.fr/ad/x/123")

    def test_parse_datetime(self):
        # Heure de Paris -> UTC : +2 h en été, +1 h en hiver
        self.assertEqual(es.parse_datetime("Vente le samedi 17 octobre 2026 à 14h00"), "2026-10-17T12:00:00Z")
        self.assertEqual(es.parse_datetime("Vente live 21/10/2026 10:30"), "2026-10-21T08:30:00Z")
        self.assertEqual(es.parse_datetime("endet am 05.12.2026, 20:15 Uhr"), "2026-12-05T19:15:00Z")
        self.assertEqual(es.parse_datetime("1er août 2026"), "2026-07-31T22:00:00Z")
        self.assertEqual(es.parse_datetime("Heute, 14:32"), "")

    def test_site_from_sender(self):
        self.assertEqual(es.site_from_sender("leboncoin <no-reply@leboncoin.fr>"), "leboncoin")
        self.assertEqual(es.site_from_sender("eBay <ebay@ebay.fr>"), "ebay")
        self.assertEqual(es.site_from_sender("eBay <ebay@ebay.de>"), "ebay")
        self.assertEqual(es.site_from_sender("Kleinanzeigen <noreply@kleinanzeigen.de>"), "kleinanzeigen")
        self.assertEqual(es.site_from_sender("eBay Kleinanzeigen <noreply@ebay-kleinanzeigen.de>"), "kleinanzeigen")
        self.assertEqual(es.site_from_sender("Interenchères <alertes@interencheres.com>"), "interencheres")
        self.assertIsNone(es.site_from_sender("Maman <maman@example.fr>"))

    def test_site_from_links(self):
        for name, site in [("alerte_lbc.html", "leboncoin"), ("alerte_ebay.html", "ebay"),
                           ("alerte_interencheres.html", "interencheres"), ("alerte_kleinanzeigen.html", "kleinanzeigen")]:
            self.assertEqual(es.site_from_links(fixture(name)), site, name)
        self.assertIsNone(es.site_from_links("<a href='https://example.com'>x</a>"))


class EbayTest(unittest.TestCase):
    def setUp(self):
        self.listings = es.parse_alert_html(fixture("alerte_ebay.html"), site="ebay")

    def test_annonces(self):
        self.assertEqual([l.price for l in self.listings], [1234.56, 45.0, 30.0])
        self.assertTrue(all(l.source == "ebay" for l in self.listings))
        first = self.listings[0]
        self.assertEqual(first.title, "Veste Carhartt Detroit J97 marron taille L")
        # redirection rover + lien direct avec paramètres de suivi : même annonce, adresse propre
        self.assertEqual(first.url, "https://www.ebay.fr/itm/296512345678")
        self.assertEqual(first.key, "ebay:https://www.ebay.fr/itm/296512345678")
        self.assertEqual(first.image, "https://i.ebayimg.com/images/g/abc/s-l225.jpg")
        self.assertEqual(first.location, "France")

    def test_port_et_fin(self):
        first, second, third = self.listings
        self.assertEqual(first.buy_cost, 1241.06)     # prix + 6,50 € de port
        self.assertEqual(second.buy_cost, 45.0)       # livraison gratuite
        self.assertIsNone(third.buy_cost)             # port inconnu : calcul par défaut
        self.assertEqual(second.ends_at, "2026-10-12T16:30:00Z")
        self.assertEqual(third.url, "https://www.ebay.fr/itm/126612345678")  # lien ?u= déroulé

    def test_ebay_de(self):
        html = ('<a href="https://www.ebay.de/itm/Levis-501/204512345678?hash=x"><img src="https://i.ebayimg.com/1.jpg">'
                "Levi's 501 Jeans W32 L32 vintage</a><p>EUR 19,99</p><p>+EUR 5,49 Versand</p><p>Aus Deutschland</p>")
        [l] = es.parse_alert_html(html, site="ebay")
        self.assertEqual((l.price, l.buy_cost, l.location), (19.99, 25.48, "Deutschland"))
        self.assertEqual(l.url, "https://www.ebay.de/itm/204512345678")


class InterencheresTest(unittest.TestCase):
    def setUp(self):
        self.listings = es.parse_alert_html(fixture("alerte_interencheres.html"), site="interencheres")

    def test_lots(self):
        self.assertEqual(len(self.listings), 3)
        estim, mise, catalogue = self.listings
        self.assertEqual(estim.title, "RAY-BAN Lot de 3 paires de lunettes de soleil")
        self.assertEqual(estim.price, 30.0)        # estimation basse
        self.assertEqual(mise.price, 20.0)         # mise à prix
        self.assertEqual(catalogue.price, 1200.0)  # « Estimation 1.200 € - 1.500 € »
        self.assertEqual(estim.url, "https://www.interencheres.com/mode-accessoires/vente-courante-mode-512345/lot-61234567.html")
        self.assertEqual(estim.image, "https://medias.interencheres.com/lots/61234567/1.jpg")
        self.assertTrue(all(l.source == "interencheres" for l in self.listings))

    def test_frais_et_date(self):
        estim, mise, catalogue = self.listings
        self.assertAlmostEqual(estim.buy_cost, round(30 * (1 + es.INTERENCHERES_FEES), 2))
        self.assertEqual(estim.ends_at, "2026-10-17T12:00:00Z")    # en-tête de la vente
        self.assertEqual(mise.ends_at, "2026-10-17T12:00:00Z")     # pas l'en-tête de la vente suivante
        self.assertEqual(catalogue.ends_at, "2026-10-21T08:30:00Z")
        self.assertEqual(estim.location, "Hôtel des ventes de Bordeaux (33)")
        self.assertEqual(catalogue.location, "Maison de ventes Millon Lyon (69)")


class KleinanzeigenTest(unittest.TestCase):
    def test_annonces(self):
        listings = es.parse_alert_html(fixture("alerte_kleinanzeigen.html"), site="kleinanzeigen")
        self.assertEqual([l.price for l in listings], [45.0, 1200.0])  # « VB » sans prix ignoré
        first = listings[0]
        self.assertEqual(first.title, "Carhartt Detroit Jacket braun Gr. L")
        self.assertEqual(first.url, "https://www.kleinanzeigen.de/s-anzeige/carhartt-detroit-jacket-braun-gr-l/2876543210-160-3331")
        self.assertTrue(first.image.startswith("https://img.kleinanzeigen.de/"))
        self.assertEqual(first.location, "10115 Berlin Mitte")
        self.assertIsNone(first.buy_cost)
        self.assertEqual(listings[1].url, "https://www.kleinanzeigen.de/s-anzeige/carhartt-vintage-jacke/2876543211-160-1234")
        self.assertEqual(listings[1].location, "80331 München Altstadt")


class DispatchTest(unittest.TestCase):
    def test_leboncoin_par_defaut(self):
        html = fixture("alerte_lbc.html")
        self.assertEqual([l.price for l in es.parse_alert_html(html)], [25, 1200, 15])
        self.assertEqual([l.price for l in es.parse_alert_html(html, site=None)], [25, 1200, 15])

    def test_parse_message(self):
        site, listings = es.parse_message(make_email("eBay <ebay@ebay.fr>", fixture("alerte_ebay.html")))
        self.assertEqual((site, len(listings)), ("ebay", 3))
        # email transféré : le site est deviné d'après les liens
        site, listings = es.parse_message(make_email("moi@example.fr", fixture("alerte_kleinanzeigen.html")))
        self.assertEqual((site, len(listings)), ("kleinanzeigen", 2))
        self.assertEqual(es.parse_message(make_email("moi@example.fr", "<p>bonjour</p>")), (None, []))

    def test_commande_parse_eml(self):
        with tempfile.TemporaryDirectory() as d:
            for sender, name, n in [("Interenchères <alertes@interencheres.com>", "alerte_interencheres.html", 3),
                                    ("leboncoin <no-reply@leboncoin.fr>", "alerte_lbc.html", 3)]:
                path = Path(d) / "alerte.eml"
                path.write_bytes(make_email(sender, fixture(name)).as_bytes())
                self.assertEqual(len(read_listings(str(path))), n)
            html = Path(d) / "alerte.html"
            html.write_text(fixture("alerte_ebay.html"), encoding="utf-8")
            self.assertEqual({l.source for l in read_listings(str(html))}, {"ebay"})


class FakeIMAP:
    """Boîte IMAP en mémoire : numéro -> email, avec les drapeaux posés."""

    def __init__(self, messages):
        self.messages = messages
        self.flagged = []
        self.searches = []

    def __call__(self, host, timeout=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        pass

    def select(self, folder, readonly=False):
        pass

    def search(self, charset, *criteria):
        self.searches.append(criteria)
        return "OK", [b" ".join(str(n).encode() for n in self.messages)]

    def fetch(self, num, what):
        msg = self.messages[int(num)]
        raw = msg.as_bytes()
        if "HEADER" in what:
            raw = f"From: {msg['From']}\r\n\r\n".encode()
        return "OK", [(b"", raw)]

    def store(self, num, flags, value):
        self.flagged.append(int(num))


class ImapTest(unittest.TestCase):
    def test_fetch_alerts_multi_sites(self):
        imap = FakeIMAP({
            1: make_email("leboncoin <no-reply@leboncoin.fr>", fixture("alerte_lbc.html"), "lbc"),
            2: make_email("Newsletter <news@example.com>", "<a href='https://www.ebay.fr/itm/123456789012'>x 3 €</a>"),
            3: make_email("eBay <ebay@ebay.de>", fixture("alerte_ebay.html"), "ebay"),
            4: make_email("Kleinanzeigen <noreply@kleinanzeigen.de>", fixture("alerte_kleinanzeigen.html"), "ka"),
        })
        env = {"IMAP_HOST": "h", "IMAP_USER": "u", "IMAP_PASSWORD": "p", "IMAP_SINCE_DAYS": "3", "IMAP_FROM": ""}
        with mock.patch.dict(os.environ, env), mock.patch.object(es.imaplib, "IMAP4_SSL", imap):
            alerts = list(es.fetch_alerts())
        self.assertEqual([s for s, _ in alerts], ["lbc", "ebay", "ka"])
        self.assertEqual(imap.flagged, [1, 3, 4])  # la newsletter reste non lue
        criteria = imap.searches[0]
        self.assertEqual(criteria[:2], ("UNSEEN", "SINCE"))

    def test_senders_query(self):
        with mock.patch.dict(os.environ, {"IMAP_FROM": "moi@example.fr"}):
            q = es._senders_query()
        self.assertEqual(q.count("OR "), 4)
        for w in ("leboncoin", "ebay", "interencheres", "kleinanzeigen", "moi@example.fr"):
            self.assertIn(f'FROM "{w}"', q)


if __name__ == "__main__":
    unittest.main()

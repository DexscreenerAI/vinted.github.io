import http.client
import json
import threading
import unittest
from pathlib import Path

import yaml

from finder.core import best_deal, import_listings
from finder.models import Listing
from finder.scoring import Config
from finder.store import Seen
from finder.web import make_server

ROOT = Path(__file__).resolve().parent.parent
CFG = Config.from_dict(yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8")))


class AccessoiresTest(unittest.TestCase):
    """Un accessoire ne doit jamais recevoir la cote de l'objet (ex. « Étui Contax T2 » coté comme un T2)."""

    def rule(self, title, price=30):
        best = best_deal(Listing(title, price), CFG)
        return best[0].rule_name if best else None

    def test_accessoires_ecartes(self):
        for t in ["Étui cuir Contax T2", "Flash pour Contax T2", "Dos dateur Contax T2 data back",
                  "Objectif Canon FD 50mm pour AE-1", "Écusson patch Carhartt Detroit",
                  "Coque de remplacement Game Boy Advance SP", "Pellicule Kodak pour Olympus mju II",
                  "Manette GameCube officielle pour console", "Doudoune North Face Nuptse fille 12 ans",
                  "Game Boy Color HS ne s'allume pas", "Levi's 501 selvedge made in Turkey"]:
            self.assertIsNone(self.rule(t), t)

    def test_objets_reconnus(self):
        self.assertEqual(self.rule("Contax T2 titane + étui", 200), "Contax T2 / T3")
        self.assertEqual(self.rule("Canon AE-1 program + objectif 50mm", 50), "Canon AE-1 / AE-1 Program")
        self.assertEqual(self.rule("Veste Carhartt Detroit vintage", 25), "Carhartt Detroit jacket")
        self.assertEqual(self.rule("Carhart detroit jacket marron", 25), "Carhartt Detroit jacket")  # faute de frappe

    def test_lot_de_jeux_pas_console(self):
        # annonce réelle signalée : 3 jeux (≈ 12 €) cotés comme une console (70 €)
        self.assertIsNone(self.rule("3 Jeux Game Boy COLOR ( WAVE RACE F-1 RACE et ROAD CHAMPS )", 5))
        self.assertIsNone(self.rule("Cartouche Game Boy Color Tetris", 8))
        self.assertEqual(self.rule("Console Game Boy Color violette + 3 jeux", 25), "Game Boy Color console")
        self.assertEqual(self.rule("Gameboy color + jeu tetris", 25), "Game Boy Color console")

    def test_jeu_pas_console(self):
        self.assertEqual(self.rule("Pokémon version Or Game Boy Color", 12), "Pokémon Or/Argent/Cristal (GBC)")


class ImportSecuriteTest(unittest.TestCase):
    def test_liens_javascript_refuses(self):
        seen = Seen(":memory:")
        res = import_listings([{"title": "Veste Carhartt Detroit", "price": 20, "url": "javascript:alert(1)"},
                               {"title": "Veste Carhartt Detroit", "price": 20, "url": "https://evil.com/ad/1"},
                               {"title": "Veste Carhartt Detroit", "price": 20,
                                "url": "https://www.leboncoin.fr/ad/vetements/123456789", "image": "javascript:x"}],
                              "leboncoin", CFG, seen)
        self.assertEqual(res["matched"], 1)
        self.assertEqual(res["deals"][0]["image"], "")


class CsrfTest(unittest.TestCase):
    """Un autre site ouvert dans le navigateur ne doit pas pouvoir piloter le logiciel local."""

    @classmethod
    def setUpClass(cls):
        cls.server = make_server(CFG, Seen(":memory:"), 0, env_path="/nonexistent/.env", host="127.0.0.1")
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def post(self, headers, body=b'{"key": "x", "status": "ignore"}', path="/api/status"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", path, body=body, headers=headers)
        return c.getresponse().status

    def test_texte_brut_refuse(self):
        self.assertEqual(self.post({"Content-Type": "text/plain"}), 403)

    def test_autre_site_refuse(self):
        self.assertEqual(self.post({"Content-Type": "application/json", "Origin": "https://evil.com"}), 403)

    def test_dns_rebinding_refuse(self):
        self.assertEqual(self.post({"Content-Type": "application/json", "Host": f"evil.com:{self.port}"}), 403)

    def test_page_et_extension_acceptees(self):
        ok = {"Content-Type": "application/json"}
        self.assertNotEqual(self.post(dict(ok, Origin=f"http://127.0.0.1:{self.port}")), 403)
        self.assertNotEqual(self.post(dict(ok, Origin="chrome-extension://abcdef")), 403)


class TourneeTest(unittest.TestCase):
    def test_tournee_complete(self):
        from finder import web
        web.SEEN = Seen(":memory:")
        web.TOUR.update(active=False, done=0)
        first = web.tour_start(CFG, ["leboncoin", "vinted"])
        self.assertEqual((first["i"], first["n"]), (1, 20))  # 10 meilleurs × 2 sites
        self.assertIn("leboncoin.fr/recherche", first["url"])
        self.assertIn("vinted.fr/catalog", web.tour_step(1)["url"])  # même article sur l'autre site
        self.assertEqual(web.tour_step(-1)["i"], 1)
        for _ in range(19):
            last = web.tour_step(1)
        end = web.tour_step(1)
        self.assertTrue(end["finished"])
        self.assertEqual(web.TOUR["done"], 1)
        self.assertFalse(web.tour_step(1)["ok"])  # plus de tournée en cours


class MiseAJourReglesTest(unittest.TestCase):
    def test_upgrade_garde_les_choix(self):
        import tempfile
        from finder.sales import set_ref_price, upgrade_config
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.yaml"
            import re
            bundled_version = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))["config_version"]
            old = re.sub(r"config_version: \d+", "config_version: 2", (ROOT / "config.example.yaml").read_text(encoding="utf-8"))
            p.write_text(old.replace("min_profit: 10", "min_profit: 15"), encoding="utf-8")
            set_ref_price(str(p), "Olympus mju II", 205)
            self.assertTrue(upgrade_config(str(p), str(ROOT / "config.example.yaml")))
            new = yaml.safe_load(p.read_text(encoding="utf-8"))
            rules = {r["name"]: r for r in new["rules"]}
            self.assertEqual((new["config_version"], new["min_profit"], rules["Olympus mju II"]["ref_price"]),
                             (bundled_version, 15, 205))
            self.assertTrue((Path(d) / "config.ancien-2.yaml").exists())
            self.assertFalse(upgrade_config(str(p), str(ROOT / "config.example.yaml")))  # déjà à jour

    def test_rescore_retire_les_jeux_cotes_comme_console(self):
        from finder.core import rescore
        seen = Seen(":memory:")
        old = Config.from_dict({"rules": [{"name": "Game Boy Color console", "all": ["game boy color"], "ref_price": 70}]})
        for i, t in enumerate(["Jeu Oui-Oui au Pays des Jouets - Game Boy Color", "Game Boy Color violette"]):
            l = Listing(t, 5 if i == 0 else 25, f"https://www.leboncoin.fr/ad/x/32840016{i:02d}")
            seen.add(l.key, t, l.price)
            seen.save_deal(*best_deal(l, old))
        self.assertEqual(rescore(CFG, seen), (1, 1))
        self.assertEqual([d["title"] for d in seen.deals("toutes")], ["Game Boy Color violette"])


class AvisIaAutoTest(unittest.TestCase):
    """Une bonne affaire attend le verdict de Claude ; « à éviter » ou état insuffisant = retirée des bonnes affaires."""

    def run_case(self, verdict):
        import os
        import time
        from unittest import mock
        from finder import ai
        from finder.core import register
        seen, notified = Seen(":memory:"), []
        listing = Listing("Veste Carhartt Detroit vintage", 20, "https://www.leboncoin.fr/ad/vetements/1234567890")
        deal, good = best_deal(listing, CFG)
        self.assertTrue(good)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x", "AI_AUTO": "1"}), \
                mock.patch.object(ai, "analyze", lambda d, e="tres_bon": dict(verdict)), \
                mock.patch.object(ai, "_started", __import__("threading").Event()), \
                mock.patch.object(ai, "_queue", __import__("queue").Queue()):
            ai.start_worker(seen, CFG, notified.append)
            seen.add(listing.key, listing.title, 20)
            register(deal, good, seen)
            self.assertEqual(seen.deals("bonnes"), [])  # en attente du verdict
            for _ in range(50):
                d = seen.get_deal(listing.key)
                if d and not d["ai_pending"]:
                    break
                time.sleep(0.05)
        return seen.deals("bonnes"), notified

    BASE = {"confiance": "moyenne", "resume": "", "correspond_au_modele": True, "authenticite": "", "etat": "",
            "revente_estimee": 80, "signaux_alerte": [], "questions_vendeur": []}

    def test_verdict_acheter(self):
        bonnes, notified = self.run_case(dict(self.BASE, verdict="acheter", etat_note="tres_bon"))
        self.assertEqual(len(bonnes), 1)
        self.assertEqual(len(notified), 1)

    def test_etat_insuffisant(self):
        bonnes, notified = self.run_case(dict(self.BASE, verdict="acheter", etat_note="usage"))
        self.assertEqual((bonnes, notified), ([], []))

    def test_verdict_passer(self):
        bonnes, _ = self.run_case(dict(self.BASE, verdict="passer", etat_note="tres_bon"))
        self.assertEqual(bonnes, [])


class EtatTitreTest(unittest.TestCase):
    def test_defauts_ecartes(self):
        self.assertIsNone(best_deal(Listing("Veste Carhartt Detroit tachée", 20), CFG))
        self.assertIsNotNone(best_deal(Listing("Veste Carhartt Detroit sans tache", 20), CFG))


class SyntaxeJsTest(unittest.TestCase):
    """Le JavaScript de la page et de l'extension doit au moins être syntaxiquement valide (si node est installé)."""

    def test_syntaxe(self):
        import shutil
        import subprocess
        import tempfile
        node = shutil.which("node")
        if not node:
            self.skipTest("node absent")
        page = (ROOT / "finder" / "page.html").read_text(encoding="utf-8")
        scripts = [page[page.index("<script>") + 8:page.rindex("</script>")]]
        scripts += [f.read_text(encoding="utf-8") for f in (ROOT / "extension").glob("*.js")]
        for code in scripts:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(code)
            r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[:500])


if __name__ == "__main__":
    unittest.main()

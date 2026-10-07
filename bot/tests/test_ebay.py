"""Source eBay : HTTP simulé (aucun appel réseau)."""

import io
import json
import os
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from finder import ebay_source as eb
from finder.scoring import Config, Rule

ENV = {"EBAY_CLIENT_ID": "app", "EBAY_CLIENT_SECRET": "cert"}
T0 = 1_791_400_000.0  # 2026-10-07 19:06:40 UTC


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url, code, body=b"{}"):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


class FakeEbay:
    """Remplace urlopen : enregistre les requêtes, renvoie un jeton puis des résultats de recherche."""

    def __init__(self, items=None, search_errors=()):
        self.requests = []
        self.items = items or []
        self.search_errors = list(search_errors)

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if req.full_url == eb.TOKEN_URL:
            return FakeResp(json.dumps({"access_token": "tok", "expires_in": 7200}).encode())
        if self.search_errors:
            code = self.search_errors.pop(0)
            raise http_error(req.full_url, code)
        return FakeResp(json.dumps({"itemSummaries": self.items}).encode())

    def searches(self):
        return [r for r in self.requests if r.full_url.startswith(eb.SEARCH_URL)]

    def tokens(self):
        return [r for r in self.requests if r.full_url == eb.TOKEN_URL]


def params(req):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))


class EbayTestCase(unittest.TestCase):
    def setUp(self):
        eb.reset()
        self.now = T0
        for p in (mock.patch.dict(os.environ, ENV), mock.patch.object(eb, "_now", lambda: self.now)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("EBAY_INTERVAL_MIN", None)

    def run_with(self, fake, cfg):
        with mock.patch("urllib.request.urlopen", fake):
            return eb.fetch_listings(cfg)


class TokenTest(EbayTestCase):
    def test_token_cached_until_5_min_before_expiry(self):
        fake = FakeEbay()
        with mock.patch("urllib.request.urlopen", fake):
            self.assertEqual(eb.get_token(), "tok")
            self.now += 7200 - 301
            eb.get_token()
            self.assertEqual(len(fake.tokens()), 1)
            self.now += 2
            eb.get_token()
            self.assertEqual(len(fake.tokens()), 2)
        req = fake.tokens()[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Basic YXBwOmNlcnQ=")  # base64("app:cert")
        body = dict(urllib.parse.parse_qsl(req.data.decode()))
        self.assertEqual(body, {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"})

    def test_bad_keys(self):
        def refuse(req, timeout=None):
            raise http_error(req.full_url, 401, b'{"error":"invalid_client"}')
        with mock.patch("urllib.request.urlopen", refuse):
            with self.assertRaisesRegex(RuntimeError, "Clés eBay refusées"):
                eb.get_token()

    def test_expired_token_is_renewed_once(self):
        fake = FakeEbay([{"title": "x", "price": {"value": "5"}}], search_errors=[401])
        cfg = Config(rules=[Rule("R", 50, search="carhartt")])
        self.assertEqual(len(self.run_with(fake, cfg)), 2)
        self.assertEqual(len(fake.tokens()), 2)


class QueryTest(EbayTestCase):
    def test_queries(self):
        self.assertEqual(eb.rule_queries(Rule("a", 1, all=["carhartt"], any=["detroit", "j97"])), ["carhartt detroit"])
        self.assertEqual(eb.rule_queries(Rule("b", 1, all=[["nintendo 64", "n64"]], any=["console"])),
                         ["nintendo 64 console"])
        self.assertEqual(eb.rule_queries(Rule("c", 1, any=["rrl", "double rl"])), ["rrl"])
        self.assertEqual(eb.rule_queries(Rule("s", 1, all=["seiko"], any=["seiko 5"])), ["seiko 5"])
        self.assertEqual(eb.rule_queries(Rule("d", 1, all=["carhartt"], search="veste carhartt detroit",
                                              variants=["carhart detroit", "veste carhartt detroit"])),
                         ["veste carhartt detroit", "carhart detroit"])

    def test_params(self):
        rule = Rule("a", 85, all=["carhartt"], any=["detroit"], max_buy=34)
        fixed = eb.build_params(rule, "carhartt detroit", "fixed")
        self.assertEqual(fixed["sort"], "newlyListed")
        self.assertEqual(fixed["filter"],
                         "itemLocationCountry:FR,price:[..34],priceCurrency:EUR,buyingOptions:{FIXED_PRICE}")
        auction = eb.build_params(rule, "carhartt detroit", "auction")
        self.assertEqual(auction["sort"], "endingSoonest")
        self.assertIn("buyingOptions:{AUCTION},bidCount:[..2],itemEndDate:[..2026-10-07T22:06:40Z]", auction["filter"])
        self.assertNotIn("price:", eb.build_params(Rule("lot", 15), "lot", "fixed")["filter"])

    def test_headers(self):
        fake = FakeEbay()
        with mock.patch.dict(os.environ, {"EBAY_MARKETPLACE": "EBAY_IT"}):
            self.run_with(fake, Config(rules=[Rule("R", 50, search="x")]))
        req = fake.searches()[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")
        self.assertEqual(req.get_header("X-ebay-c-marketplace-id"), "EBAY_IT")


class MappingTest(unittest.TestCase):
    def test_protection_fee(self):
        self.assertEqual(eb.buyer_protection_fee(20), 1.50)        # exemple eBay : 0,10 + 1,40
        self.assertEqual(eb.buyer_protection_fee(1500), 36.70)     # exemple eBay : 0,10 + 36,60
        self.assertEqual(eb.buyer_protection_fee(10), 0.80)

    def test_fixed_price_private_seller(self):
        l = eb.to_listing({
            "title": "Veste Carhartt Detroit", "price": {"value": "30.00", "currency": "EUR"},
            "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"], "itemWebUrl": "https://www.ebay.fr/itm/1",
            "image": {"imageUrl": "https://i.ebayimg.com/1.jpg"},
            "itemLocation": {"city": "Lyon", "postalCode": "69***", "country": "FR"},
            "itemEndDate": "2026-11-01T10:00:00.000Z",
            "seller": {"sellerAccountType": "INDIVIDUAL"},
            "shippingOptions": [{"shippingCost": {"value": "5.50", "currency": "EUR"}}],
        })
        self.assertEqual((l.price, l.source, l.ends_at, l.location), (30.0, "ebay", "", "Lyon 69***"))
        self.assertEqual(l.url, "https://www.ebay.fr/itm/1")
        self.assertEqual(l.image, "https://i.ebayimg.com/1.jpg")
        self.assertEqual(l.buy_cost, 30 + 5.5 + 0.10 + 1.40 + 0.40)

    def test_auction_business_seller_no_shipping(self):
        l = eb.to_listing({
            "title": "Game Boy Color", "price": {"value": "1.00"}, "currentBidPrice": {"value": "12.50"},
            "buyingOptions": ["AUCTION"], "bidCount": 1, "itemEndDate": "2026-10-07T22:00:00.000Z",
            "seller": {"sellerAccountType": "BUSINESS"},
        })
        self.assertEqual(l.price, 12.5)
        self.assertEqual(l.ends_at, "2026-10-07T22:00:00.000Z")
        self.assertEqual(l.buy_cost, 12.5)
        self.assertIn("retrait", l.location)
        self.assertTrue(l.key.startswith("ebay:"))

    def test_incomplete_item_ignored(self):
        self.assertIsNone(eb.to_listing({"title": "x"}))


class SchedulerTest(EbayTestCase):
    def cfg(self, n):
        return Config(rules=[Rule(f"R{i}", 50, search=f"q{i}") for i in range(n)])

    def test_rotation_batch_and_interval(self):
        cfg, fake = self.cfg(3), FakeEbay()  # 3 règles x 2 passes = 6 tâches
        self.run_with(fake, cfg)
        self.assertEqual(len(fake.searches()), eb.BATCH)
        self.now += 120
        self.run_with(fake, cfg)
        self.assertEqual(len(fake.searches()), 6)  # les 2 restantes, puis plus rien avant 30 min
        done = {(params(r)["q"], params(r)["sort"]) for r in fake.searches()}
        self.assertEqual(len(done), 6)
        self.now += 120
        self.run_with(fake, cfg)
        self.assertEqual(len(fake.searches()), 6)
        self.now += 30 * 60
        self.run_with(fake, cfg)
        self.assertEqual(len(fake.searches()), 6 + eb.BATCH)

    def test_interval_stretched_to_fit_daily_quota(self):
        self.assertEqual(eb.interval_s(10), 30 * 60)
        self.assertLessEqual(86400 / eb.interval_s(128) * 128, eb.DAILY_LIMIT)

    def test_daily_counter_and_reset(self):
        cfg, fake = self.cfg(3), FakeEbay()
        eb._state.day = "2026-10-07"
        eb._state.calls = eb.DAILY_LIMIT
        with self.assertRaisesRegex(RuntimeError, "Quota eBay atteint"):
            self.run_with(fake, cfg)
        self.assertEqual(fake.searches(), [])
        self.now += 5 * 3600  # minuit UTC passé
        self.run_with(fake, cfg)
        self.assertEqual(eb._state.calls, eb.BATCH)

    def test_http_429_and_results_kept(self):
        cfg = self.cfg(3)
        fake = FakeEbay([{"title": "a", "price": {"value": "5"}, "itemWebUrl": "u1"}])
        calls = {"n": 0}

        def flaky(req, timeout=None):
            if req.full_url.startswith(eb.SEARCH_URL):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise http_error(req.full_url, 429)
            return fake(req, timeout)
        with mock.patch("urllib.request.urlopen", flaky):
            with self.assertRaisesRegex(RuntimeError, "Quota eBay atteint"):
                eb.fetch_listings(cfg)
            self.now += 120
            got = eb.fetch_listings(cfg)
        self.assertEqual(sum(1 for l in got if l.url == "u1"), 1 + 4)  # 1 gardée avant l'erreur + 4 nouvelles


if __name__ == "__main__":
    unittest.main()

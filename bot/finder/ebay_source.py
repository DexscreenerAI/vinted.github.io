"""Source eBay via l'API officielle Browse (clés développeur gratuites : developer.ebay.com).

Interface utilisée par core.py :
  configured() -> bool                 # les clés EBAY_CLIENT_ID / EBAY_CLIENT_SECRET sont-elles définies ?
  fetch_listings(cfg) -> list[Listing] # nouvelles annonces à évaluer (source="ebay")

Quota : eBay autorise ~5 000 appels Browse par jour. Chaque règle donne deux recherches
(prix fixe récent + enchères qui finissent bientôt), par requête (search + variants).
fetch_listings ne fait qu'un petit lot de recherches à chaque passage (toutes les ~2 min),
les plus en retard d'abord, et allonge l'intervalle si besoin pour rester sous DAILY_LIMIT.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .models import Listing
from .scoring import Config, Rule

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"
TIMEOUT = 20            # secondes par requête HTTP
DAILY_LIMIT = 4000      # recherches par jour (eBay : ~5 000), marge pour les relances
BATCH = 4               # recherches max par passage de la boucle principale
PAGE_SIZE = 50
AUCTION_WINDOW_H = 3    # enchères qui se terminent dans les 3 prochaines heures
AUCTION_MAX_BIDS = 2    # peu d'enchérisseurs = chance de l'avoir bas

# Frais de Protection acheteurs eBay.fr, payés par l'acheteur sur les objets de vendeurs PARTICULIERS
# (compte en France ou en Italie) depuis le 1er septembre 2026 : 0,10 € fixe + 7 % jusqu'à 20 €,
# 4 % de 20 à 300 €, 2 % de 300 à 4 000 €, calculés sur le prix hors livraison.
# Source : https://www.ebay.fr/help/buying/paying-items/buyer-protection-fee?id=5594 (consulté en octobre 2026).
# L'API Browse renvoie le prix sans ces frais : on les ajoute nous-mêmes.
EBAY_BUYER_PROTECTION_FIXED = 0.10
EBAY_BUYER_PROTECTION_TIERS = [(20.0, 0.07), (300.0, 0.04), (4000.0, 0.02)]  # (jusqu'à, taux)


def configured() -> bool:
    return bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))


def _now() -> float:
    return time.time()


class _State:
    """État en mémoire (perdu au redémarrage, sans conséquence)."""

    def __init__(self):
        self.token: Optional[str] = None
        self.token_until = 0.0
        self.token_keys: Tuple[str, str] = ("", "")
        self.day = ""                       # jour UTC du compteur
        self.calls = 0                      # recherches faites ce jour-là
        self.last_run: Dict[tuple, float] = {}  # tâche -> heure de la dernière recherche réussie
        self.pending: List[Listing] = []    # annonces récupérées avant une erreur, rendues au passage suivant


_state = _State()


def reset() -> None:
    """Remet l'état à zéro (tests, changement de clés)."""
    global _state
    _state = _State()


# ---------- HTTP ----------

def _error_message(e: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(e.read().decode("utf-8", "replace"))
        errs = data.get("errors") or [{}]
        return errs[0].get("longMessage") or errs[0].get("message") or data.get("error_description", "")
    except Exception:
        return ""


def _request(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError("Quota eBay atteint (trop d'appels aujourd'hui) : reprise automatique plus tard") from None
        if e.code in (401, 403):  # traité par l'appelant (clés refusées / jeton expiré)
            raise
        detail = _error_message(e)
        raise RuntimeError(f"Erreur eBay {e.code}" + (f" : {detail}" if detail else "")) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"eBay injoignable : {e.reason}") from None


def _bad_keys() -> RuntimeError:
    return RuntimeError("Clés eBay refusées : vérifiez l'App ID et le Cert ID (clés « Production ») dans Réglages → eBay")


def get_token() -> str:
    """Jeton d'application (client credentials), gardé en mémoire jusqu'à 5 min avant expiration."""
    keys = (os.environ.get("EBAY_CLIENT_ID", ""), os.environ.get("EBAY_CLIENT_SECRET", ""))
    st = _state
    if st.token and st.token_keys == keys and _now() < st.token_until:
        return st.token
    basic = base64.b64encode(f"{keys[0]}:{keys[1]}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        data = _request(req)
    except urllib.error.HTTPError:
        raise _bad_keys() from None
    st.token, st.token_keys = data["access_token"], keys
    st.token_until = _now() + int(data.get("expires_in", 7200)) - 300
    return st.token


def _search(params: dict) -> dict:
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    country = os.environ.get("EBAY_COUNTRY", "FR")
    for attempt in (1, 2):
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {get_token()}",
            "X-EBAY-C-MARKETPLACE-ID": os.environ.get("EBAY_MARKETPLACE", "EBAY_FR"),
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=" + urllib.parse.quote(f"country={country}"),
            "Accept-Language": "fr-FR",
        })
        try:
            return _request(req)
        except urllib.error.HTTPError:
            _state.token = None  # jeton expiré ou révoqué : on en redemande un, une seule fois
            if attempt == 2:
                raise _bad_keys() from None
    return {}


# ---------- Requêtes ----------

def rule_queries(rule: Rule) -> List[str]:
    """Requête principale (search, sinon déduite de all + premier any) puis les variantes."""
    q = rule.search.strip()
    if not q:
        words = [w[0] if isinstance(w, list) else w for w in rule.all if w]
        if rule.any:
            words.append(rule.any[0])
        tokens = " ".join(str(w) for w in words if w).split()
        q = " ".join(dict.fromkeys(tokens))  # sans doublon : « seiko seiko 5 » -> « seiko 5 »
    out = []
    for v in [q] + list(rule.variants or []):
        v = str(v).strip()[:100]  # eBay tronque au-delà de 100 caractères
        if v and v not in out:
            out.append(v)
    return out


def build_params(rule: Rule, query: str, mode: str) -> dict:
    """Paramètres d'une recherche ; mode = "fixed" (prix fixe récents) ou "auction" (enchères bientôt finies)."""
    filters = [f"itemLocationCountry:{os.environ.get('EBAY_COUNTRY', 'FR')}"]
    if rule.max_buy is not None:
        filters += [f"price:[..{rule.max_buy:g}]", "priceCurrency:EUR"]
    if mode == "auction":
        end = datetime.fromtimestamp(_now(), timezone.utc) + timedelta(hours=AUCTION_WINDOW_H)
        filters += ["buyingOptions:{AUCTION}", f"bidCount:[..{AUCTION_MAX_BIDS}]",
                    f"itemEndDate:[..{end.strftime('%Y-%m-%dT%H:%M:%SZ')}]"]
        sort = "endingSoonest"
    else:
        filters.append("buyingOptions:{FIXED_PRICE}")
        sort = "newlyListed"
    return {"q": query, "filter": ",".join(filters), "sort": sort,
            "limit": PAGE_SIZE, "fieldgroups": "EXTENDED"}  # EXTENDED : ajoute la ville


# ---------- Conversion ----------

def buyer_protection_fee(price: float) -> float:
    """Frais de Protection acheteurs eBay.fr pour un objet de particulier."""
    fee, low = EBAY_BUYER_PROTECTION_FIXED, 0.0
    for high, rate in EBAY_BUYER_PROTECTION_TIERS:
        if price > low:
            fee += (min(price, high) - low) * rate
        low = high
    return round(fee, 2)


def _amount(obj) -> Optional[float]:
    try:
        return float(obj["value"])
    except (TypeError, KeyError, ValueError):
        return None


def to_listing(item: dict) -> Optional[Listing]:
    auction = "AUCTION" in (item.get("buyingOptions") or [])
    price = _amount(item.get("currentBidPrice")) if auction else None
    if price is None:
        price = _amount(item.get("price"))
    if price is None or not item.get("title"):
        return None

    loc = item.get("itemLocation") or {}
    location = " ".join(p for p in (loc.get("city"), loc.get("postalCode")) if p)
    shipping = None
    for opt in item.get("shippingOptions") or []:
        shipping = _amount(opt.get("shippingCost"))
        if shipping is not None:
            break
    if shipping is None:  # pas de port indiqué : retrait sur place probable
        shipping = 0.0
        location = (location + " · " if location else "") + "port non indiqué (retrait ?)"

    buy_cost = price + shipping
    # Prudent : frais ajoutés sauf vendeur professionnel déclaré (champ absent = on suppose particulier)
    if (item.get("seller") or {}).get("sellerAccountType") != "BUSINESS":
        buy_cost += buyer_protection_fee(price)

    return Listing(
        title=item["title"], price=price, url=item.get("itemWebUrl", ""),
        image=(item.get("image") or {}).get("imageUrl", ""), location=location, source="ebay",
        ends_at=item.get("itemEndDate", "") if auction else "", buy_cost=round(buy_cost, 2),
    )


# ---------- Planification ----------

def _tasks(cfg: Config) -> List[Tuple[tuple, Rule, str, str]]:
    return [((rule.name, q, mode), rule, q, mode)
            for rule in cfg.rules for q in rule_queries(rule) for mode in ("fixed", "auction")]


def interval_s(n_tasks: int) -> float:
    """Délai entre deux recherches d'une même tâche : EBAY_INTERVAL_MIN, allongé si le quota l'exige."""
    try:
        minutes = float(os.environ.get("EBAY_INTERVAL_MIN", "30"))
    except ValueError:
        minutes = 30.0
    return max(minutes * 60, 86400 * n_tasks / DAILY_LIMIT)


def fetch_listings(cfg: Config) -> List[Listing]:
    st = _state
    today = time.strftime("%Y-%m-%d", time.gmtime(_now()))
    if st.day != today:  # nouveau jour UTC : compteur remis à zéro
        st.day, st.calls = today, 0

    out, st.pending = st.pending, []
    tasks = _tasks(cfg)
    wait = interval_s(len(tasks))
    now = _now()
    due = [t for t in tasks if now - st.last_run.get(t[0], 0.0) >= wait]
    due.sort(key=lambda t: st.last_run.get(t[0], 0.0))  # jamais faites / les plus anciennes d'abord

    for key, rule, query, mode in due[:BATCH]:
        if st.calls >= DAILY_LIMIT:
            st.pending = out
            raise RuntimeError(f"Quota eBay atteint ({DAILY_LIMIT} recherches aujourd'hui) : reprise à minuit UTC")
        st.calls += 1
        try:
            data = _search(build_params(rule, query, mode))
        except Exception:
            st.pending = out  # rien de perdu : rendu au prochain passage
            raise
        st.last_run[key] = _now()
        out += [l for l in map(to_listing, data.get("itemSummaries") or []) if l]
    return out

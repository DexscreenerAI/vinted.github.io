"""Lecture des emails d'alerte (recherches sauvegardées) de plusieurs sites.

Sites reconnus : Leboncoin, eBay (ebay.fr / ebay.de), Interenchères (alertes
par mots-clés sur les lots des maisons de vente) et Kleinanzeigen.de
(« Suchauftrag »). Le bot ne fait aucune requête sur ces sites : il lit
seulement les alertes qu'ils vous envoient par email.

Le HTML des emails change de temps en temps et n'a pas pu être vérifié sur de
vrais emails pour eBay, Interenchères et Kleinanzeigen : les analyseurs sont
volontairement tolérants. Vérifiez sur un vrai email avec
`python -m finder parse alerte.eml`.
"""

import datetime as dt
import email
import imaplib
import os
import re
import unicodedata
from email.message import Message
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Callable, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from .models import Listing
from .text import parse_price

# Frais acheteur Interenchères : fixés par chaque maison de vente, en général
# 20-25 % HT soit 24-30 % TTC du prix au marteau (ex. 20 % HT = 24 % TTC,
# 23 % HT = 27,5 % TTC, 27,6 % TTC chez d'autres), plus une commission de
# plateforme pour les enchères en live (~3 % HT). On prend 28 % TTC.
# Sources : conditions de vente de maisons sur Interenchères/Drouot (aguttes.com,
# debaecque.fr) et margeoapp.com/blog/acheter-aux-encheres-interencheres-drouot.
# Le transport (ou le retrait sur place) n'est PAS compté : il varie trop.
INTERENCHERES_FEES = 0.28

SITES = ("leboncoin", "ebay", "interencheres", "kleinanzeigen")

# Expéditeur -> site (kleinanzeigen avant ebay : ancien domaine « ebay-kleinanzeigen.de »)
_SENDERS = (("kleinanzeigen", "kleinanzeigen"), ("interencheres", "interencheres"),
            ("leboncoin", "leboncoin"), ("ebay", "ebay"))

# Paramètres des liens de suivi qui contiennent la vraie adresse
_REDIRECT_PARAMS = ("url", "loc", "mpre", "u", "target", "redirect", "redirect_url", "dest", "destination", "link", "r")

_NUM = r"\d{1,3}(?:[   .,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_AMOUNT_RE = re.compile(rf"(?:€|\bEUR)\s?({_NUM})|({_NUM})\s?(?:€|EUR\b)", re.I)
_ESTIM_RE = re.compile(rf"estimation\s*:?\s*({_NUM})\s*(?:€|EUR)?\s*(?:-|–|à|/)\s*(?:{_NUM})", re.I)
_START_RE = re.compile(rf"mise\s+[aà]\s+prix\s*:?\s*(?:€|EUR)?\s*({_NUM})", re.I)
_SHIP_RE = re.compile(r"livraison|frais de port|\bport\b|versand|shipping|postage|envoi", re.I)
_FREE_SHIP_RE = re.compile(r"gratuit|kostenlo|free", re.I)
_END_WORDS = re.compile(r"termine|fin\b|fin de|endet|ends|vente|enchere|auktion|live|le \d", re.I)

_MONTHS = {"janv": 1, "jan": 1, "fevr": 2, "fev": 2, "feb": 2, "mars": 3, "mar": 3, "marz": 3, "avr": 4, "apr": 4,
           "mai": 5, "may": 5, "juin": 6, "jun": 6, "juil": 7, "jul": 7, "aout": 8, "aug": 8, "sept": 9, "sep": 9,
           "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12}
_DATE_NUM_RE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2})\b(?:\D{1,8}?(\d{1,2})\s?[h:]\s?(\d{2})?)?")
_DATE_TXT_RE = re.compile(r"\b(\d{1,2})(?:er)?\.?\s+([a-z]{3,9})\.?\s+(\d{4})\b(?:\D{1,8}?(\d{1,2})\s?[h:]\s?(\d{2})?)?")

_SKIP_TEXT = {"voir l'annonce", "voir", "modifier ma recherche", "se desabonner"}
_SKIP_WORDS = re.compile(r"^(voir|afficher|zur anzeige|ansehen|jetzt|acheter|kaufen|ench[eé]rir|"
                         r"offre directe|sofort-kaufen|achat imm[eé]diat)\b", re.I)


# --- Outils communs -----------------------------------------------------------

def amount(text: str) -> Optional[float]:
    """Premier montant en euros : « 1.234,56 € », « EUR 12,00 », « 12 € VB », « 1 200 € »."""
    m = _AMOUNT_RE.search(text or "")
    return _to_float(m.group(1) or m.group(2)) if m else None


def _to_float(num: str) -> float:
    num = re.sub(r"[   ]", "", num)
    if "," in num and "." in num:  # le dernier séparateur est la décimale
        dec = "," if num.rfind(",") > num.rfind(".") else "."
        num = num.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif re.search(r"[.,]\d{3}$", num):  # « 1.234 » ou « 1,234 » : milliers
        num = re.sub(r"[.,]", "", num)
    else:
        num = num.replace(",", ".")
    return float(num)


def unwrap_url(href: str) -> str:
    """Retire les redirections de suivi (?url=, loc=, mpre=…) pour retrouver la vraie adresse."""
    href = (href or "").strip()
    for _ in range(3):
        try:
            query = parse_qs(urlsplit(href).query)
        except ValueError:
            break
        inner = next((v[0] for k in _REDIRECT_PARAMS for v in [query.get(k)] if v and
                      unquote(v[0]).startswith(("http://", "https://"))), None)
        if not inner:
            break
        href = inner if inner.startswith(("http://", "https://")) else unquote(inner)  # encodé deux fois
    return href


def _paris_to_utc(local: dt.datetime) -> dt.datetime:
    """Heure de Paris/Berlin -> UTC (heure d'été du dernier dimanche de mars à celui d'octobre)."""
    def last_sunday(month):
        d = dt.date(local.year, month, 31)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    summer = dt.datetime.combine(last_sunday(3), dt.time(2)) <= local < dt.datetime.combine(last_sunday(10), dt.time(3))
    return local - dt.timedelta(hours=2 if summer else 1)


def _fold(text: str) -> str:
    """Minuscules sans accents, ponctuation gardée (« août » -> « aout »)."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def parse_datetime(text: str) -> str:
    """Date/heure (FR ou DE) d'un texte -> ISO 8601 UTC, ou "" ; « 12/10/2026 à 14h30 », « 12 octobre 2026 14:30 »."""
    t = _fold(text)
    m = _DATE_NUM_RE.search(t)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour, minute = m.group(4), m.group(5)
    else:
        m = _DATE_TXT_RE.search(t)
        if not m:
            return ""
        month = next((n for k, n in _MONTHS.items() if m.group(2).startswith(k)), None)
        if month is None:
            return ""
        day, year, hour, minute = int(m.group(1)), int(m.group(3)), m.group(4), m.group(5)
    if year < 100:
        year += 2000
    try:
        local = dt.datetime(year, month, day, int(hour or 0), int(minute or 0))
    except ValueError:
        return ""
    return _paris_to_utc(local).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Regroupement des textes par lien d'annonce --------------------------------

class _AlertParser(HTMLParser):
    """Regroupe les textes et images de l'email par lien d'annonce.

    `match(href)` renvoie l'adresse normalisée de l'annonce (ou None si le lien
    n'en est pas une) : la photo et le titre, souvent deux liens de suivi
    différents, retombent ainsi sur le même bloc. On garde aussi quelques
    textes juste après (prix, ville) et juste avant (date de vente…).
    """

    def __init__(self, match: Callable[[str], Optional[str]], after: int = 3):
        super().__init__()
        self.match = match
        self.n_after = after
        self.blocks: List[dict] = []
        self._current = None
        self._after = None  # bloc fermé dont on capte encore quelques textes
        self._recent: List[str] = []  # derniers textes hors annonce
        self._dated = ""  # dernier texte daté hors annonce (en-tête de vente…)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            url = self.match(attrs.get("href") or "")
            if url:
                self._current = self._block_for(url)
                self._after = None
        elif tag == "img" and self._current is not None:
            if not self._current["image"] and attrs.get("src"):
                self._current["image"] = attrs["src"]
                if attrs.get("alt"):
                    self._current["alt"] = " ".join(attrs["alt"].split())

    def handle_endtag(self, tag):
        if tag == "a" and self._current is not None:
            self._after = (self._current, self.n_after)
            self._current = None

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        if self._current is not None:
            self._current["texts"].append(text)
            return
        if self._after is not None:
            block, remaining = self._after
            block["after"].append(text)
            self._after = (block, remaining - 1) if remaining > 1 else None
        self._recent = (self._recent + [text])[-6:]
        if parse_datetime(text):
            self._dated = text

    def _block_for(self, url):
        for b in self.blocks:
            if b["href"] == url:
                return b
        b = {"href": url, "texts": [], "after": [], "before": list(self._recent), "dated": self._dated, "image": "", "alt": ""}
        self.blocks.append(b)
        return b


def _blocks(html: str, match, after: int = 5) -> List[dict]:
    parser = _AlertParser(match, after)
    parser.feed(html)
    return parser.blocks


def _title(texts: List[str], alt: str = "") -> str:
    candidates = [t for t in texts if amount(t) is None and parse_price(t) is None and len(t) > 3
                  and t.lower() not in _SKIP_TEXT and not _SKIP_WORDS.match(t)
                  and not _SHIP_RE.search(t) and not re.match(r"(estimation|mise [aà] prix|vb$)", t, re.I)]
    if not candidates and alt and len(alt) > 3:
        candidates = [alt]
    return max(candidates, key=len) if candidates else ""


# --- Leboncoin ----------------------------------------------------------------

def _match_leboncoin(href: str) -> Optional[str]:
    return href if ("leboncoin" in href or "lbc" in href) else None


def parse_leboncoin(html: str) -> List[Listing]:
    listings = []
    for b in _blocks(html, _match_leboncoin, after=3):
        price = None
        for t in b["texts"] + b["after"]:
            price = parse_price(t)
            if price is not None:
                break
        titles = [
            t for t in b["texts"]
            if parse_price(t) is None and t.lower() not in _SKIP_TEXT and len(t) > 3
        ]
        if price is None or not titles:
            continue  # lien de menu / pied de page, pas une annonce
        title = max(titles, key=len)
        location = next((t for t in b["after"] if parse_price(t) is None), "")
        listings.append(Listing(title=title, price=price, url=b["href"], image=b["image"], location=location))
    return listings


# --- eBay (recherches sauvegardées ebay.fr / ebay.de) --------------------------

_EBAY_ITEM_RE = re.compile(r"https?://(?:[\w-]+\.)*ebay\.([a-z.]{2,6})/itm/(?:[^/?#]+/)?(\d{9,15})")


def _match_ebay(href: str) -> Optional[str]:
    m = _EBAY_ITEM_RE.search(unwrap_url(href))
    return f"https://www.ebay.{m.group(1)}/itm/{m.group(2)}" if m else None


def parse_ebay(html: str) -> List[Listing]:
    """Coût d'achat = prix + port quand le port figure dans l'email (« Livraison gratuite » = 0).

    Sinon buy_cost reste None : le calcul par défaut (prix + frais + port forfaitaire,
    réglages de config.yaml) sert d'estimation prudente.
    """
    listings = []
    for b in _blocks(html, _match_ebay):
        texts = b["texts"] + b["after"]
        price = next((amount(t) for t in texts if amount(t) is not None and not _SHIP_RE.search(t)), None)
        title = _title(b["texts"], b["alt"])
        if price is None or not title:
            continue
        shipping = None
        for t in texts:
            if _SHIP_RE.search(t):
                shipping = 0.0 if _FREE_SHIP_RE.search(t) else amount(t)
                if shipping is not None:
                    break
        ends_at = next((parse_datetime(t) for t in texts if _END_WORDS.search(t) and parse_datetime(t)), "")
        location = next((re.sub(r"^(?:lieu[^:]*:|standort:?|artikelstandort:?|de|aus|from)\s*", "", t, flags=re.I)
                         for t in b["after"] if len(t) < 50 and re.match(r"(de|aus|from)\s+\w|lieu|standort|artikelstandort", t, re.I)), "")
        listings.append(Listing(
            title=title, price=price, url=b["href"], image=b["image"], location=location, source="ebay",
            ends_at=ends_at, buy_cost=round(price + shipping, 2) if shipping is not None else None,
        ))
    return listings


# --- Interenchères (alertes mots-clés sur les lots) -----------------------------

_INTER_LOT_RE = re.compile(r"https?://(?:[\w-]+\.)*interencheres\.com/[^?#\s]*lot-\d+[^?#\s]*")


def _match_interencheres(href: str) -> Optional[str]:
    m = _INTER_LOT_RE.search(unwrap_url(href))
    return m.group(0) if m else None


def parse_interencheres(html: str) -> List[Listing]:
    """Prix = estimation basse, sinon mise à prix, sinon premier montant.

    buy_cost = prix × (1 + INTERENCHERES_FEES) : frais acheteur inclus, transport non compris.
    ends_at = date et heure de la vente si l'email les donne (dans le lot ou dans l'en-tête de vente).
    """
    listings = []
    for b in _blocks(html, _match_interencheres, after=2):  # au-delà : en-tête de la vente suivante
        texts = b["texts"] + b["after"]
        joined = " | ".join(texts)
        m = _ESTIM_RE.search(joined) or _START_RE.search(joined)
        price = _to_float(m.group(1)) if m else next((amount(t) for t in texts if amount(t) is not None), None)
        title = _title(b["texts"], b["alt"])
        if price is None or not title:
            continue
        ends_at = next((parse_datetime(t) for t in texts + [b["dated"]] if parse_datetime(t)), "")
        location = next((t for t in b["texts"] + b["before"][::-1] if amount(t) is None and not parse_datetime(t)
                         and re.search(r"h[oô]tel des ventes|maison de vente|commissaire|\(\d{2,3}\)|\b\d{5}\b|svv|enchères", t, re.I)), "")
        listings.append(Listing(
            title=title, price=price, url=b["href"], image=b["image"], location=location,
            source="interencheres", ends_at=ends_at, buy_cost=round(price * (1 + INTERENCHERES_FEES), 2),
        ))
    return listings


# --- Kleinanzeigen.de (Suchauftrag) ----------------------------------------------

_KA_AD_RE = re.compile(r"https?://(?:[\w-]+\.)*(?:ebay-)?kleinanzeigen\.de/s-anzeige/[^?#\s]+")


def _match_kleinanzeigen(href: str) -> Optional[str]:
    m = _KA_AD_RE.search(unwrap_url(href))
    return m.group(0).replace("ebay-kleinanzeigen.de", "kleinanzeigen.de") if m else None


def parse_kleinanzeigen(html: str) -> List[Listing]:
    """« 45 € VB » (VB = prix négociable) est lu comme 45 €.

    buy_cost reste None : remise en main propre ou envoi + « Sicher bezahlen » selon
    le vendeur, le calcul par défaut (proche de Leboncoin) sert d'estimation.
    """
    listings = []
    for b in _blocks(html, _match_kleinanzeigen):
        texts = b["texts"] + b["after"]
        price = next((amount(t) for t in texts if amount(t) is not None), None)
        title = _title(b["texts"], b["alt"])
        if price is None or not title:
            continue
        location = next((t for t in texts if re.match(r"\d{5}\s+\S", t)), "") or \
            next((t for t in b["after"] if amount(t) is None and not re.match(r"(heute|gestern)\b", t, re.I)), "")
        listings.append(Listing(title=title, price=price, url=b["href"], image=b["image"],
                                location=location, source="kleinanzeigen"))
    return listings


PARSERS = {
    "leboncoin": parse_leboncoin,
    "ebay": parse_ebay,
    "interencheres": parse_interencheres,
    "kleinanzeigen": parse_kleinanzeigen,
}
_MATCHERS = {"leboncoin": _match_leboncoin, "ebay": _match_ebay,
             "interencheres": _match_interencheres, "kleinanzeigen": _match_kleinanzeigen}


# --- Détection du site et dispatch ---------------------------------------------

def site_from_sender(sender: str) -> Optional[str]:
    addr = (parseaddr(sender or "")[1] or sender or "").lower()
    return next((site for word, site in _SENDERS if word in addr), None)


def site_from_links(html: str) -> Optional[str]:
    """Site dont les liens d'annonce sont les plus nombreux dans l'email (emails transférés…)."""
    hrefs = re.findall(r"""href\s*=\s*["']([^"']+)""", html or "", re.I)
    counts = {site: sum(1 for h in hrefs if site != "leboncoin" and m(h.replace("&amp;", "&")))
              for site, m in _MATCHERS.items()}
    counts["leboncoin"] = sum(1 for h in hrefs if "leboncoin.fr" in h)
    best = max(counts, key=counts.get)
    return best if counts[best] else None


def detect_site(sender: str = "", html: str = "") -> Optional[str]:
    return site_from_sender(sender) or site_from_links(html)


def parse_alert_html(html: str, site: Optional[str] = "leboncoin") -> List[Listing]:
    """Annonces d'un email d'alerte. site=None : détection d'après les liens."""
    site = site or site_from_links(html)
    return PARSERS[site](html) if site in PARSERS else []


def html_part(msg: Message) -> str:
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def parse_message(msg: Message, links_fallback: bool = True) -> Tuple[Optional[str], List[Listing]]:
    """(site, annonces) d'un email ; site None = expéditeur inconnu."""
    html = html_part(msg)
    site = site_from_sender(msg.get("From", ""))
    if site is None and links_fallback:
        site = site_from_links(html)
    return site, (PARSERS[site](html) if site else [])


# --- Lecture IMAP ----------------------------------------------------------------

def configured() -> bool:
    return bool(os.environ.get("IMAP_HOST"))


def fetch_listings(cfg=None) -> Iterator[Listing]:
    """Interface commune des sources : toutes les annonces des nouveaux emails d'alerte."""
    for subject, listings in fetch_alerts():
        site = listings[0].source if listings else "?"
        print(f"[alerte {site}] {subject} : {len(listings)} annonce(s)")
        yield from listings


def _senders_query() -> str:
    """Critère IMAP « depuis l'un des sites connus » (OR binaire en notation préfixée)."""
    words = [w for w, _ in _SENDERS]
    extra = os.environ.get("IMAP_FROM", "").strip()
    if extra and extra.lower() not in words:
        words.append(extra)
    query = f'FROM "{words[-1]}"'
    for w in reversed(words[:-1]):
        query = f'OR FROM "{w}" {query}'
    return query


def _login(imap, host: str, user: str, password: str) -> None:
    """Connexion IMAP avec un message d'erreur compréhensible (cas Gmail surtout)."""
    if "gmail" in host or "google" in host:
        password = password.replace(" ", "")  # Google affiche le code par blocs : « abcd efgh ijkl mnop »
    try:
        imap.login(user.strip(), password)
    except imaplib.IMAP4.error as e:
        msg = e.args[0].decode(errors="replace") if e.args and isinstance(e.args[0], bytes) else str(e)
        if "AUTHENTICATIONFAILED" in msg.upper() or "invalid credentials" in msg.lower():
            if "gmail" in host or "google" in host:
                raise RuntimeError(
                    "Gmail refuse le mot de passe. Il faut un « mot de passe d'application » de 16 lettres, pas le "
                    "mot de passe habituel : activez la validation en deux étapes sur myaccount.google.com/security, "
                    "puis créez le code sur myaccount.google.com/apppasswords. Vérifiez aussi que l'IMAP est activé "
                    "dans Gmail (Paramètres → Transfert et POP/IMAP).") from None
            raise RuntimeError("Adresse ou mot de passe refusé par le serveur mail. Beaucoup de messageries exigent un "
                               "« mot de passe d'application » (ou « mot de passe tiers ») à créer dans les "
                               "paramètres de sécurité du compte.") from None
        raise RuntimeError(f"Connexion à la boîte mail refusée : {msg}") from None


def test_login(host: str, user: str, password: str) -> int:
    """Vérifie l'accès IMAP ; renvoie le nombre d'emails d'alerte (tous sites) trouvés dans la boîte."""
    with imaplib.IMAP4_SSL(host, timeout=20) as imap:
        _login(imap, host, user, password)
        imap.select(os.environ.get("IMAP_FOLDER", "INBOX"), readonly=True)
        _, data = imap.search(None, _senders_query())
        return len(data[0].split())


def _since_days() -> int:
    try:
        return max(0, int(os.environ.get("IMAP_SINCE_DAYS", "7")))
    except ValueError:
        return 7


def fetch_alerts(mark_seen: bool = True) -> Iterator[Tuple[str, List[Listing]]]:
    """Récupère les emails d'alerte non lus via IMAP (variables d'environnement IMAP_*).

    On cherche les non lus des IMAP_SINCE_DAYS derniers jours (7 par défaut, 0 = sans limite),
    on lit d'abord l'expéditeur seul, et seuls les emails d'un site connu sont téléchargés,
    analysés puis marqués lus. Les autres restent non lus. IMAP_FROM (facultatif) :
    expéditeur supplémentaire (ex. vos transferts), dont le site est deviné d'après les liens.
    """
    host = os.environ["IMAP_HOST"]
    user = os.environ["IMAP_USER"]
    password = os.environ["IMAP_PASSWORD"]
    folder = os.environ.get("IMAP_FOLDER", "INBOX")
    extra = os.environ.get("IMAP_FROM", "").strip().lower()

    criteria = ["UNSEEN"]
    days = _since_days()
    if days:
        since = dt.date.today() - dt.timedelta(days=days)
        criteria += ["SINCE", f"{since.day}-{since.strftime('%b')}-{since.year}"]

    with imaplib.IMAP4_SSL(host, timeout=30) as imap:
        _login(imap, host, user, password)
        imap.select(folder)
        _, data = imap.search(None, *criteria)
        for num in data[0].split():
            _, head = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            sender = email.message_from_bytes(head[0][1]).get("From", "")
            known = site_from_sender(sender)
            if known is None and not (extra and extra in sender.lower()):
                continue  # expéditeur inconnu : on n'y touche pas (reste non lu)
            _, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            site, listings = parse_message(msg)
            if site is None:
                continue
            yield msg.get("Subject", ""), listings
            if mark_seen:
                imap.store(num, "+FLAGS", "\\Seen")

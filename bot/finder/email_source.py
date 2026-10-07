"""Lecture des emails d'alerte Leboncoin (recherches sauvegardées).

Le bot ne fait aucune requête sur Leboncoin : il lit seulement les alertes que
Leboncoin vous envoie par email.
"""

import email
import imaplib
import os
from email.message import Message
from html.parser import HTMLParser
from typing import Iterator, List, Tuple

from .models import Listing
from .text import parse_price

_SKIP_TEXT = {"voir l'annonce", "voir", "modifier ma recherche", "se desabonner"}


class _AlertParser(HTMLParser):
    """Regroupe les textes et images de l'email par lien d'annonce.

    Le HTML exact des emails Leboncoin change de temps en temps : on reste
    volontairement tolérant. Chaque lien contient en général la photo, le
    titre et le prix ; sinon le prix est pris dans le texte juste après.
    """

    def __init__(self):
        super().__init__()
        self.blocks: List[dict] = []
        self._current = None
        self._after = None  # bloc fermé dont on capte encore quelques textes

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            href = attrs.get("href") or ""
            if "leboncoin" in href or "lbc" in href:
                self._current = self._block_for(href)
                self._after = None
        elif tag == "img" and self._current is not None:
            if not self._current["image"] and attrs.get("src"):
                self._current["image"] = attrs["src"]

    def handle_endtag(self, tag):
        if tag == "a" and self._current is not None:
            self._after = (self._current, 3)
            self._current = None

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        if self._current is not None:
            self._current["texts"].append(text)
        elif self._after is not None:
            block, remaining = self._after
            block["after"].append(text)
            self._after = (block, remaining - 1) if remaining > 1 else None

    def _block_for(self, href):
        for b in self.blocks:
            if b["href"] == href:
                return b
        b = {"href": href, "texts": [], "after": [], "image": ""}
        self.blocks.append(b)
        return b


def parse_alert_html(html: str) -> List[Listing]:
    parser = _AlertParser()
    parser.feed(html)
    listings = []
    for b in parser.blocks:
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


def html_part(msg: Message) -> str:
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def fetch_alerts(mark_seen: bool = True) -> Iterator[Tuple[str, List[Listing]]]:
    """Récupère les emails Leboncoin non lus via IMAP (variables d'environnement IMAP_*)."""
    host = os.environ["IMAP_HOST"]
    user = os.environ["IMAP_USER"]
    password = os.environ["IMAP_PASSWORD"]
    folder = os.environ.get("IMAP_FOLDER", "INBOX")
    sender = os.environ.get("IMAP_FROM", "leboncoin")

    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select(folder)
        _, data = imap.search(None, "UNSEEN", "FROM", f'"{sender}"')
        for num in data[0].split():
            _, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            yield msg.get("Subject", ""), parse_alert_html(html_part(msg))
            if mark_seen:
                imap.store(num, "+FLAGS", "\\Seen")

import html
import json
import os
import urllib.request

from .models import Deal


def format_deal(deal: Deal) -> str:
    l = deal.listing
    lines = [
        f"🔥 <b>{html.escape(l.title)}</b>",
        f"Règle : {html.escape(deal.rule_name)}",
        f"Prix LBC : {l.price:.0f} € → coût d'achat {deal.buy_cost:.2f} €",
        f"Revente estimée : {deal.est_resale:.0f} €"
        + (f" ({deal.pieces} pièces vendables)" if deal.pieces > 1 else ""),
        f"Multiplicateur : <b>x{deal.ratio:.1f}</b> · bénéfice net ≈ <b>{deal.net_profit:.0f} €</b>",
    ]
    if l.location:
        lines.append(f"📍 {html.escape(l.location)}")
    for n in deal.notes:
        lines.append(f"⚠️ {html.escape(n)}")
    if l.url:
        lines.append(f'<a href="{html.escape(l.url)}">Voir l\'annonce</a>')
    return "\n".join(lines)


def send(deal: Deal) -> None:
    """Envoie sur Telegram si TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID sont définis, sinon affiche."""
    text = format_deal(deal)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print(text.replace("<b>", "").replace("</b>", ""), end="\n\n")
        return
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=15).read()

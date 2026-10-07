"""Avis de Claude sur une affaire : analyse de la photo et de l'annonce avant d'acheter.

Nécessite une clé API Anthropic (console.anthropic.com), à coller dans Réglages.
Chaque analyse est payante (quelques centimes) : elle n'est lancée qu'à la demande.
"""

import base64
import json
import os
import re
import urllib.request

# Modèle : Claude Haiku 5.5 par défaut (rapide, ~0,05 centime par avis), modifiable via AI_MODEL
MODEL = "claude-haiku-5-5"
MEDIA_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")
MAX_IMAGE_BYTES = 5 * 1024 * 1024

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["acheter", "a_verifier", "passer"]},
        "confiance": {"type": "string", "enum": ["faible", "moyenne", "elevee"]},
        "resume": {"type": "string"},
        "correspond_au_modele": {"type": "boolean"},
        "authenticite": {"type": "string"},
        "etat": {"type": "string"},
        "etat_note": {"type": "string", "enum": ["neuf", "tres_bon", "bon", "usage", "mauvais", "inconnu"]},
        "revente_estimee": {"type": "number"},
        "signaux_alerte": {"type": "array", "items": {"type": "string"}},
        "questions_vendeur": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "confiance", "resume", "correspond_au_modele", "authenticite", "etat", "etat_note",
                 "revente_estimee", "signaux_alerte", "questions_vendeur"],
    "additionalProperties": False,
}

SYSTEM = (
    "Tu es un revendeur expérimenté de seconde main en France (achat sur Leboncoin, Vinted ou eBay, "
    "revente sur Vinted). On te montre une annonce repérée par un outil qui l'estime rentable. "
    "Ton rôle : dire honnêtement si elle vaut le coup avant que l'utilisateur contacte le vendeur. "
    "Vérifie sur la photo et le titre : que l'objet est bien le modèle annoncé (pas un modèle voisin moins "
    "recherché, ni une version enfant ou femme si ce n'est pas précisé), les signes de contrefaçon "
    "visibles pour ce modèle, l'état apparent (taches, usure, pièces manquantes, écran rayé…), et si le "
    "prix de revente estimé est réaliste pour cet état. Une seule photo ne suffit souvent pas à conclure : "
    "dans ce cas, réponds « a_verifier » et dis précisément quoi demander au vendeur (photos d'étiquette, "
    "de défauts, test de fonctionnement…). N'invente rien que tu ne vois pas. Réponds en français, "
    "phrases courtes ; revente_estimee en euros, réaliste pour un prix réellement vendu sur Vinted."
)


def configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _image_url(url: str) -> str:
    # Leboncoin sert parfois de l'AVIF (non lu par l'API) : on demande la version JPEG
    return re.sub(r"(rule=[\w-]*?)-(avif|webp)\b", r"\1-jpg", url or "")


def fetch_image(url: str):
    """(media_type, base64) de la photo de l'annonce, ou None si indisponible."""
    url = _image_url(url)
    if not url.startswith(("http://", "https://")):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/jpeg,image/png,image/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        media = (r.headers.get_content_type() or "").lower()
        data = r.read(MAX_IMAGE_BYTES + 1)
    if media == "image/jpg":
        media = "image/jpeg"
    if media not in MEDIA_TYPES or len(data) > MAX_IMAGE_BYTES:
        return None
    return media, base64.standard_b64encode(data).decode("ascii")


ETATS = {"neuf": "neuf (jamais utilisé)", "tres_bon": "très bon état ou neuf", "bon": "bon état au minimum",
         "tous": "n'importe quel état"}


def analyze(deal: dict, etat_minimum: str = "tres_bon") -> dict:
    """Avis de Claude sur une affaire (ligne de la table deals)."""
    import anthropic

    content = []
    image = None
    try:
        image = fetch_image(deal.get("image") or "")
    except Exception:
        image = None
    if image:
        content.append({"type": "image", "source": {"type": "base64", "media_type": image[0], "data": image[1]}})
    source = deal.get("source") or "leboncoin"
    content.append({"type": "text", "text": (
        f"Annonce {source} : « {deal.get('title')} »\n"
        f"Prix demandé : {deal.get('price')} € (coût d'achat estimé avec frais et port : {deal.get('buy_cost')} €)\n"
        f"Lieu : {deal.get('location') or 'inconnu'}\n"
        f"Article reconnu par l'outil : {deal.get('rule')} (catégorie {deal.get('category')})\n"
        f"Revente estimée par l'outil (prix moyen de la règle, à vérifier) : {deal.get('est_resale')} € "
        f"({deal.get('pieces') or 1} pièce(s) vendable(s)), multiplicateur x{deal.get('ratio')}\n"
        + ("" if image else "Pas de photo disponible : juge sur le titre et dis-le.\n")
        + f"L'acheteur n'achète que du {ETATS.get(etat_minimum, ETATS['tres_bon'])} : si l'état visible est inférieur, "
          "verdict « passer » ; si l'état ne se voit pas, etat_note « inconnu », verdict « a_verifier » et demande "
          "des photos des défauts éventuels.\n"
        + "Cette affaire vaut-elle le coup ? revente_estimee = valeur totale réaliste de ce qui est vendu dans "
          "l'annonce (tout le lot), 0 si tu ne peux pas l'estimer."
    )})

    client = anthropic.Anthropic()
    model = os.environ.get("AI_MODEL") or MODEL
    params = dict(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if model.startswith("claude-haiku"):
        response = client.messages.create(**params)  # Haiku : pas de modèle de repli côté serveur
    else:  # Opus / Sonnet : si le modèle refuse, l'API relance sur un modèle de repli
        response = client.beta.messages.create(**params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude a refusé d'analyser cette annonce.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Réponse de Claude incomplète, réessayez.")
    text = next(b.text for b in response.content if b.type == "text")
    result = json.loads(text)
    result["photo_analysee"] = bool(image)
    return result


# ---- Avis automatique sur chaque nouvelle bonne affaire (file d'attente en arrière-plan) ----
import queue
import threading
import time

_queue: "queue.Queue[str]" = queue.Queue()
_day = {"date": "", "count": 0}
_started = threading.Event()


def auto_enabled() -> bool:
    return configured() and os.environ.get("AI_AUTO", "1") != "0"


def daily_limit() -> int:
    try:
        return max(0, int(os.environ.get("AI_DAILY_LIMIT", "300")))
    except ValueError:
        return 300


def enqueue(key: str) -> None:
    if auto_enabled():
        _queue.put(key)


def start_worker(seen, cfg, on_good) -> None:
    """Thread qui analyse les affaires en file, une à la fois, dans la limite quotidienne."""
    if _started.is_set():
        return
    _started.set()
    q = _queue

    def run():
        while True:
            key = q.get()
            today = time.strftime("%Y-%m-%d")
            if _day["date"] != today:
                _day.update(date=today, count=0)
            if not auto_enabled() or _day["count"] >= daily_limit():
                seen.status["ai_note"] = f"Avis IA automatique en pause : limite de {daily_limit()} avis par jour atteinte."
                continue
            deal = seen.get_deal(key)
            if not deal or deal.get("ai"):
                continue
            try:
                result = analyze(deal, getattr(cfg, "etat_minimum", "tres_bon"))
            except Exception as e:  # clé refusée, crédit épuisé, réseau… : l'affaire reste visible sans avis
                seen.status["ai_note"] = f"Avis IA automatique indisponible : {e}"
                seen.set_ai_pending(key, False)
                time.sleep(5)
                continue
            _day["count"] += 1
            seen.status["ai_note"] = None
            became_good = seen.apply_ai(key, result, cfg)
            if became_good:
                try:
                    on_good(seen.get_deal(key))
                except Exception:
                    pass

    threading.Thread(target=run, daemon=True).start()

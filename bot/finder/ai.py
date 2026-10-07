"""Avis de Claude sur une affaire : analyse de la photo et de l'annonce avant d'acheter.

Nécessite une clé API Anthropic (console.anthropic.com), à coller dans Réglages.
Chaque analyse est payante (quelques centimes) : elle n'est lancée qu'à la demande.
"""

import base64
import json
import os
import re
import urllib.request

MODEL = "claude-opus-5-5"
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
        "revente_estimee": {"type": "number"},
        "signaux_alerte": {"type": "array", "items": {"type": "string"}},
        "questions_vendeur": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "confiance", "resume", "correspond_au_modele", "authenticite", "etat",
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


def analyze(deal: dict) -> dict:
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
        f"Revente estimée par l'outil : {deal.get('est_resale')} € "
        f"({deal.get('pieces') or 1} pièce(s) vendable(s)), multiplicateur x{deal.get('ratio')}\n"
        + ("" if image else "Pas de photo disponible : juge sur le titre et dis-le.\n")
        + "Cette affaire vaut-elle le coup ?"
    )})

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # si le modèle refuse, l'API relance la demande sur un modèle de repli
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude a refusé d'analyser cette annonce.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Réponse de Claude incomplète, réessayez.")
    text = next(b.text for b in response.content if b.type == "text")
    result = json.loads(text)
    result["photo_analysee"] = bool(image)
    return result

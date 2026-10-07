"""Mes ventes : mise à jour de la cote (ref_price) d'une règle dans config.yaml."""

import os
import re
from pathlib import Path

import yaml

NAME_LINE = re.compile(r"^(\s*)-\s+name:\s*(.*?)\s*$")
REF_LINE = re.compile(r"^(\s*ref_price:\s*)([^\s#]+)(.*)$")


def _rule_name(raw: str) -> str:
    """Valeur YAML d'une ligne « - name: … » (guillemets et commentaire de fin gérés par YAML)."""
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw.split(" #", 1)[0].strip()
    return "" if value is None else str(value)


def set_ref_price(path: str, rule: str, value: float) -> float:
    """Remplace seulement la ligne `ref_price:` du bloc de la règle `rule`, sans toucher au reste
    (commentaires, mise en forme, fins de ligne Windows). Renvoie l'ancienne valeur."""
    p = Path(path)
    text = p.read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        m = NAME_LINE.match(line.rstrip("\r\n"))
        if m and _rule_name(m.group(2)) == rule:
            start, indent = i, len(m.group(1))
            break
    if start is None:
        raise ValueError(f"règle introuvable dans {p.name} : {rule}")
    for i in range(start + 1, len(lines)):
        body = lines[i].rstrip("\r\n")
        if NAME_LINE.match(body):
            break  # règle suivante
        stripped = body.lstrip()
        if stripped and not stripped.startswith("#") and len(body) - len(stripped) <= indent:
            break  # fin de la liste des règles (clé de premier niveau)
        m = REF_LINE.match(body)
        if m:
            old = m.group(2)
            new = str(int(value)) if float(value).is_integer() else f"{value:g}"
            lines[i] = m.group(1) + new + m.group(3) + lines[i][len(body):]
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes("".join(lines).encode("utf-8"))
            os.replace(tmp, p)
            try:
                return float(old)
            except ValueError:
                return 0.0
    raise ValueError(f"pas de ligne ref_price pour la règle : {rule}")


def upgrade_config(path: str, bundled: str) -> bool:
    """Remplace config.yaml par la nouvelle version des règles si elle est plus récente, en gardant
    les choix de l'utilisateur : ref_price / max_buy des règles qu'il a modifiées, seuils et coûts.
    L'ancien fichier est sauvegardé (config.ancien-<version>.yaml). Renvoie True si mis à jour."""
    p, b = Path(path), Path(bundled)
    if not p.exists() or not b.exists():
        return False
    old_text = p.read_bytes().decode("utf-8-sig", errors="replace")
    new_text = b.read_text(encoding="utf-8")
    try:
        old, new = yaml.safe_load(old_text) or {}, yaml.safe_load(new_text) or {}
    except yaml.YAMLError:
        return False  # fichier modifié à la main et invalide : on n'y touche pas
    if int(old.get("config_version") or 0) >= int(new.get("config_version") or 0):
        return False
    backup = p.with_name(f"config.ancien-{old.get('config_version') or 1}.yaml")
    backup.write_text(old_text, encoding="utf-8")
    out = new_text
    for key in ("min_ratio", "min_profit", "hand_delivery"):  # seuils choisis par l'utilisateur
        if key in old and old[key] != new.get(key):
            out = re.sub(rf"(?m)^({key}:\s*)[^#\n]*?(\s*(#.*)?)$", lambda m: f"{m.group(1)}{str(old[key]).lower() if isinstance(old[key], bool) else old[key]}{m.group(2)}", out, count=1)
    p.write_text(out, encoding="utf-8")
    # prix de revente et prix max modifiés par l'utilisateur (ex. « Mettre à jour la cote »)
    olds = {r.get("name"): r for r in old.get("rules") or [] if isinstance(r, dict)}
    for r in new.get("rules") or []:
        o = olds.get(r.get("name"))
        if o and o.get("ref_price") not in (None, r.get("ref_price")) and o.get("ref_price") != _old_default(o, r):
            try:
                set_ref_price(path, r["name"], float(o["ref_price"]))
            except (ValueError, KeyError):
                pass
    return True


def _old_default(old_rule: dict, new_rule: dict):
    """Un prix resté à une ancienne valeur par défaut n'a pas été choisi par l'utilisateur : il ne doit
    pas écraser la nouvelle estimation. Renvoie ce prix s'il fait partie des anciennes valeurs livrées."""
    price = old_rule.get("ref_price")
    return price if price in KNOWN_DEFAULTS.get(old_rule.get("name"), ()) else None


# Prix de revente livrés par les versions précédentes de config.example.yaml (règle -> valeurs)
KNOWN_DEFAULTS = {
 "Carhartt Detroit jacket": [
  85
 ],
 "Carhartt Active jacket": [
  90
 ],
 "Carhartt Chore coat / Michigan": [
  95
 ],
 "Carhartt Santa Fe jacket": [
  75
 ],
 "Carhartt Double Knee": [
  55
 ],
 "Levi's 501 Made in USA": [
  45
 ],
 "Levi's Big E / redline selvedge": [
  200
 ],
 "Levi's trucker vintage": [
  50
 ],
 "Ralph Lauren pull torsadé": [
  40
 ],
 "Ralph Lauren Harrington": [
  50
 ],
 "Ralph Lauren demi-zip": [
  35
 ],
 "Ralph Lauren Polo Bear": [
  110
 ],
 "RRL Double RL": [
  110
 ],
 "Lacoste vintage": [
  35
 ],
 "Tommy Hilfiger 90s": [
  32
 ],
 "Nike veste survêtement vintage": [
  35
 ],
 "Adidas veste survêtement vintage": [
  35
 ],
 "The North Face Nuptse": [
  110
 ],
 "The North Face Denali": [
  42
 ],
 "The North Face Mountain Jacket": [
  85
 ],
 "Patagonia Synchilla / Snap-T": [
  50
 ],
 "Patagonia Retro-X": [
  85
 ],
 "Arc'teryx veste": [
  170
 ],
 "Stone Island badge": [
  130
 ],
 "Barbour Beaufort / Bedale": [
  110
 ],
 "Dr Martens 1460": [
  50
 ],
 "Dr Martens Made in England": [
  110
 ],
 "Salomon XT-6": [
  105
 ],
 "New Balance 990 Made in USA": [
  120
 ],
 "New Balance 2002R": [
  80
 ],
 "Timberland 6-inch": [
  48
 ],
 "Lot vêtements marque homme": [
  15,
  25
 ],
 "Lot Petit Bateau": [
  5
 ],
 "Lot Jacadi": [
  8
 ],
 "Lot Bonpoint": [
  15
 ],
 "Game Boy Color console": [
  70
 ],
 "Game Boy Advance SP": [
  75
 ],
 "Game Boy classique DMG": [
  55
 ],
 "Nintendo GameCube console": [
  100
 ],
 "Nintendo 64 console": [
  85
 ],
 "Pokémon Rouge/Bleu/Jaune (Game Boy)": [
  30
 ],
 "Pokémon Or/Argent/Cristal (GBC)": [
  40
 ],
 "Pokémon Émeraude (GBA)": [
  85
 ],
 "Pokémon Rubis/Saphir/Rouge Feu/Vert Feuille": [
  50
 ],
 "Jeux N64 phares": [
  25
 ],
 "Jeux GameCube phares": [
  45
 ],
 "Classeur cartes Pokémon WOTC 1999-2002": [
  1.5
 ],
 "Dracaufeu set de base": [
  150
 ],
 "Lot minifigurines Lego": [
  4
 ],
 "Lego Modular / Creator Expert": [
  180
 ],
 "Longchamp Pliage": [
  45
 ],
 "Ray-Ban B&L vintage": [
  110
 ],
 "Persol 649 / 714": [
  110
 ],
 "Seiko 5 automatique vintage": [
  70
 ],
 "Seiko 6309 Turtle": [
  250
 ],
 "Casio G-Shock DW-5600 / 6900": [
  55
 ],
 "Olympus mju II": [
  180
 ],
 "Olympus Trip 35": [
  70
 ],
 "Compact argentique divers": [
  45
 ],
 "Canon AE-1 / AE-1 Program": [
  150
 ],
 "Pentax K1000 / Nikon FM2": [
  160
 ],
 "Minolta X-700": [
  100
 ],
 "Yashica T4 / T5": [
  230
 ],
 "Contax T2 / T3": [
  420,
  650
 ],
 "Carhartt Detroit / Active jacket": [
  85
 ],
 "Carhartt (autres pièces)": [
  40
 ],
 "The North Face Nuptse / doudoune 700": [
  110
 ],
 "Levi's 501 (pièce)": [
  35
 ],
 "Ralph Lauren (pièce)": [
  35
 ],
 "Lot enfant de marque": [
  7
 ],
 "Pack console rétro + jeux": [
  180
 ],
 "Classeur / lot Pokémon ancien": [
  120
 ]
}

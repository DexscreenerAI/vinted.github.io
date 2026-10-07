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

"""Source eBay via l'API officielle Browse (clés développeur gratuites : developer.ebay.com).

Interface utilisée par core.py :
  configured() -> bool                 # les clés EBAY_CLIENT_ID / EBAY_CLIENT_SECRET sont-elles définies ?
  fetch_listings(cfg) -> list[Listing] # nouvelles annonces à évaluer (source="ebay")
"""

import os
from typing import List

from .models import Listing
from .scoring import Config


def configured() -> bool:
    return bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))


def fetch_listings(cfg: Config) -> List[Listing]:
    return []  # à implémenter

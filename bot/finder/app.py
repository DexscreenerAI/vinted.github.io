"""Lancement « double-clic » : prépare les fichiers, démarre le bot et ouvre la page."""

import os
import shutil
import sys
import threading
import webbrowser
from pathlib import Path

from .core import load_config, load_env, loop
from .store import Seen
from .web import make_server


def _base_dir() -> Path:
    """Dossier de travail : à côté du .exe, sinon le dossier courant."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _bundled(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / name


def launch() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        base = _base_dir()
        os.chdir(base)
        if not Path("config.yaml").exists():
            shutil.copy(_bundled("config.example.yaml"), "config.yaml")
            print("config.yaml créé (vos niches et prix de revente).")
        load_env(".env")
        cfg = load_config("config.yaml")
        seen = Seen("finder.db")

        server = None
        for port in range(8000, 8020):
            try:
                server = make_server(cfg, seen, port, env_path=".env", config_path="config.yaml", host="127.0.0.1")
                break
            except OSError:
                continue
        if server is None:
            raise RuntimeError("aucun port libre entre 8000 et 8019")

        threading.Thread(target=loop, args=(cfg, seen, 120, "config.yaml"), daemon=True).start()
        url = f"http://localhost:{server.server_address[1]}"
        print("=" * 60)
        print("  Chasseur d'affaires Leboncoin -> Vinted")
        print(f"  Page des résultats : {url}")
        print(f"  Dossier : {base}")
        print("  Laissez cette fenêtre ouverte. Fermez-la pour arrêter.")
        print("=" * 60)
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        print(f"\nErreur : {e}")
        if getattr(sys, "frozen", False):
            input("Appuyez sur Entrée pour fermer...")
        return 1
    return 0

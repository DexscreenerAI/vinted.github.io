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


def _already_running():
    """URL du logiciel s'il tourne déjà (double-clic en trop), sinon None."""
    import json
    import urllib.request
    for port in range(8000, 8020):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.3) as r:
                if json.loads(r.read()).get("app") == "chasseur":
                    return f"http://localhost:{port}"
        except Exception:
            continue
    return None


def _disable_quickedit() -> None:
    """Windows : un clic dans la fenêtre noire (mode « Édition rapide ») gèle le programme. On le désactive."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # entrée console
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, (mode.value & ~0x0040) | 0x0080)
    except Exception:
        pass


def _install_extension_folder() -> None:
    """Copie l'extension Chrome, déjà décompressée, dans « chasseur-extension » à côté du .exe (mise à jour
    à chaque lancement) : il suffit de la charger dans chrome://extensions puis de la recharger."""
    src = _bundled("extension")
    if not getattr(sys, "frozen", False) or not src.is_dir():
        return
    try:
        dst = Path("chasseur-extension")
        dst.mkdir(exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)
    except OSError as e:
        print(f"Extension non copiée : {e}")


def launch() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    _disable_quickedit()
    running = _already_running()
    if running:
        print(f"Le logiciel est déjà lancé : ouverture de {running}")
        webbrowser.open(running)
        return 0

    try:
        base = _base_dir()
        os.chdir(base)
        if not Path("config.yaml").exists():
            shutil.copy(_bundled("config.example.yaml"), "config.yaml")
            print("config.yaml créé (vos niches et prix de revente).")
        else:
            from .sales import upgrade_config
            if upgrade_config("config.yaml", str(_bundled("config.example.yaml"))):
                print("Règles mises à jour (vos prix et réglages sont gardés, ancien fichier sauvegardé).")
        _install_extension_folder()
        load_env(".env")
        cfg = load_config("config.yaml")
        seen = Seen("finder.db")
        from .core import rescore
        n, gone = rescore(cfg, seen)  # affaires en attente réévaluées avec les règles actuelles
        if gone:
            print(f"{gone} affaire(s) retirée(s) : elles ne correspondent plus aux règles.")

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
        from . import ai
        from .core import notify_good
        ai.start_worker(seen, cfg, notify_good)  # avis IA automatique sur les bonnes affaires
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

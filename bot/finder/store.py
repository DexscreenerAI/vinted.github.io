import sqlite3


class Seen:
    """Mémorise les annonces déjà notifiées (SQLite)."""

    def __init__(self, path: str = "finder.db"):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, title TEXT, price REAL,"
            " first_seen TEXT DEFAULT CURRENT_TIMESTAMP)"
        )

    def add(self, key: str, title: str, price) -> bool:
        """True si l'annonce est nouvelle."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO seen (key, title, price) VALUES (?, ?, ?)", (key, title, price)
        )
        self.db.commit()
        return cur.rowcount == 1

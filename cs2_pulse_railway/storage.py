"""
storage.py
===========
Persistência local em SQLite: histórico de análises de combos e watchlist
de jogos/wallets seguidas.

Por defeito grava cs2_pulse.db na pasta da app (bom para correr localmente).
Em produção (Railway), define a env var DATA_DIR para apontar para um volume
persistente montado -- caso contrário, o ficheiro fica no filesystem do
contentor e perde-se em cada deploy/restart.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "cs2_pulse.db"


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_title TEXT,
                event_slug TEXT,
                run_at REAL,
                n_combos INTEGER,
                n_rows INTEGER,
                total_volume_usdc REAL,
                rows_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE,
                title TEXT,
                added_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT UNIQUE,
                label TEXT,
                added_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wallet_check_cache (
                wallet TEXT,
                event_slug TEXT,
                checked_at REAL,
                found_combo INTEGER,
                PRIMARY KEY (wallet, event_slug)
            )
        """)


def save_analysis_run(event_title: str, event_slug: str, rows: list[dict]) -> int:
    n_combos = len({r["combo_condition_id"] for r in rows}) if rows else 0
    total_volume = sum(r.get("valor_investido_usdc", 0) for r in rows)
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO analysis_runs
               (event_title, event_slug, run_at, n_combos, n_rows, total_volume_usdc, rows_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_title, event_slug, time.time(), n_combos, len(rows), total_volume,
             json.dumps(rows)),
        )
        return cur.lastrowid


def list_analysis_runs(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            """SELECT id, event_title, event_slug, run_at, n_combos, n_rows, total_volume_usdc
               FROM analysis_runs ORDER BY run_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def get_analysis_run(run_id: int) -> dict | None:
    with _conn() as conn:
        cur = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["rows"] = json.loads(d.pop("rows_json"))
        return d


def add_watchlist_game(slug: str, title: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist_games (slug, title, added_at) VALUES (?, ?, ?)",
            (slug, title, time.time()),
        )


def remove_watchlist_game(slug: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM watchlist_games WHERE slug = ?", (slug,))


def list_watchlist_games() -> list[dict]:
    with _conn() as conn:
        cur = conn.execute("SELECT * FROM watchlist_games ORDER BY added_at DESC")
        return [dict(row) for row in cur.fetchall()]


def add_watchlist_wallet(wallet: str, label: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist_wallets (wallet, label, added_at) VALUES (?, ?, ?)",
            (wallet, label, time.time()),
        )


def remove_watchlist_wallet(wallet: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM watchlist_wallets WHERE wallet = ?", (wallet,))


def list_watchlist_wallets() -> list[dict]:
    with _conn() as conn:
        cur = conn.execute("SELECT * FROM watchlist_wallets ORDER BY added_at DESC")
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Cache de verificação de wallets (para acelerar re-análises do mesmo jogo)
# ---------------------------------------------------------------------------

def get_recently_checked_negative_wallets(event_slug: str, ttl_seconds: float) -> set[str]:
    """
    Wallets verificadas para este jogo nos últimos `ttl_seconds` segundos
    que NÃO tinham combo -- podem ser saltadas numa nova análise, poupando
    tempo. Wallets que TINHAM combo nunca ficam em cache (são sempre
    reverificadas, para garantir que os dados mostrados estão atualizados).
    """
    cutoff = time.time() - ttl_seconds
    with _conn() as conn:
        cur = conn.execute(
            """SELECT wallet FROM wallet_check_cache
               WHERE event_slug = ? AND checked_at >= ? AND found_combo = 0""",
            (event_slug, cutoff),
        )
        return {row["wallet"] for row in cur.fetchall()}


def record_wallet_checks(event_slug: str, wallet_found_map: dict[str, bool]) -> None:
    """wallet_found_map: {wallet: True/False (encontrou combo ou não)}."""
    now = time.time()
    with _conn() as conn:
        conn.executemany(
            """INSERT INTO wallet_check_cache (wallet, event_slug, checked_at, found_combo)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(wallet, event_slug) DO UPDATE SET
                   checked_at = excluded.checked_at,
                   found_combo = excluded.found_combo""",
            [(w, event_slug, now, int(found)) for w, found in wallet_found_map.items()],
        )


def clear_wallet_check_cache(event_slug: str | None = None) -> None:
    with _conn() as conn:
        if event_slug:
            conn.execute("DELETE FROM wallet_check_cache WHERE event_slug = ?", (event_slug,))
        else:
            conn.execute("DELETE FROM wallet_check_cache")

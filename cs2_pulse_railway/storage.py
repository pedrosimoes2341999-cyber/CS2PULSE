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

# --- Limpeza automática (para nunca chegar perto do limite do volume) ---
# Por defeito, o volume do Railway tem 500MB. Deixamos margem: a limpeza
# dispara aos 400MB e desce até 80% desse valor (320MB), nunca apagando as
# 20 análises mais recentes, aconteça o que acontecer.
MAX_DB_BYTES = int(os.environ.get("MAX_DB_BYTES", 400 * 1024 * 1024))
TARGET_DB_BYTES = int(MAX_DB_BYTES * 0.8)
CLEANUP_MIN_INTERVAL_SECONDS = 6 * 3600  # não corre mais que de 6 em 6 horas
# O TTL máximo da cache de verificação de wallets, escolhido na UI, é de
# 120 min -- por isso qualquer entrada com mais de 24h é garantidamente
# inútil (nunca mais vai ser lida por um TTL tão curto).
WALLET_CACHE_MAX_AGE_SECONDS = 24 * 3600


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
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


# ---------------------------------------------------------------------------
# Limpeza automática
# ---------------------------------------------------------------------------

def db_size_bytes() -> int:
    try:
        return DB_PATH.stat().st_size
    except FileNotFoundError:
        return 0


def _get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def maybe_cleanup(force: bool = False) -> dict | None:
    """
    Limpeza automática, pensada para nunca deixar a base de dados chegar
    perto do limite do volume:

    1. Apaga sempre entradas antigas da cache de verificação de wallets
       (nunca são úteis passadas 24h, dado o TTL máximo da UI ser 120 min).
    2. Só se o ficheiro estiver a aproximar-se do limite (MAX_DB_BYTES):
       apaga as análises mais antigas em lotes (~10% de cada vez), com um
       VACUUM entre lotes para confirmar o tamanho real no disco (o SQLite
       não encolhe o ficheiro só por apagar linhas -- só o VACUUM liberta
       o espaço a sério). Nunca apaga abaixo de 20 análises guardadas,
       nem corre mais de 6 ciclos, para nunca ficar preso num loop.

    Por defeito só corre de 6 em 6 horas (CLEANUP_MIN_INTERVAL_SECONDS);
    usa force=True para ignorar esse intervalo (ex: botão manual na UI).

    Devolve um resumo do que foi feito, ou None se saltou por já ter
    corrido recentemente.
    """
    with _conn() as conn:
        last_run = float(_get_meta(conn, "last_cleanup_at") or 0)
        if not force and (time.time() - last_run) < CLEANUP_MIN_INTERVAL_SECONDS:
            return None
        _set_meta(conn, "last_cleanup_at", str(time.time()))

    summary = {
        "wallet_cache_deleted": 0,
        "analysis_runs_deleted": 0,
        "vacuumed": False,
        "size_before_mb": round(db_size_bytes() / (1024 * 1024), 1),
    }

    with _conn() as conn:
        cutoff = time.time() - WALLET_CACHE_MAX_AGE_SECONDS
        cur = conn.execute("DELETE FROM wallet_check_cache WHERE checked_at < ?", (cutoff,))
        summary["wallet_cache_deleted"] = cur.rowcount

    if db_size_bytes() <= MAX_DB_BYTES:
        summary["size_after_mb"] = round(db_size_bytes() / (1024 * 1024), 1)
        return summary

    for _ in range(10):
        if db_size_bytes() <= TARGET_DB_BYTES:
            break
        with _conn() as conn:
            remaining = conn.execute("SELECT COUNT(*) c FROM analysis_runs").fetchone()["c"]
            if remaining <= 20:
                break
            batch = max(1, remaining // 4)
            ids = [
                r["id"] for r in conn.execute(
                    "SELECT id FROM analysis_runs ORDER BY run_at ASC LIMIT ?", (batch,)
                ).fetchall()
            ]
            conn.executemany("DELETE FROM analysis_runs WHERE id = ?", [(i,) for i in ids])
            summary["analysis_runs_deleted"] += len(ids)

        with sqlite3.connect(DB_PATH) as vconn:
            vconn.execute("VACUUM")
        summary["vacuumed"] = True

    summary["size_after_mb"] = round(db_size_bytes() / (1024 * 1024), 1)
    return summary

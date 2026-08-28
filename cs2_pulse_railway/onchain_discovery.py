"""
liquipedia_client.py
======================
Cliente para o calendário e resultados de CS2 via API pública da Liquipedia
(action=ask, Semantic MediaWiki). Não precisa de API key.

IMPORTANTE -- Termos de uso da Liquipedia (liquipedia.net/api-terms-of-use):
  - Máximo 1 pedido a cada 2 segundos.
  - User-Agent identificável obrigatório (nome da app + contacto).
  - Conteúdo licenciado CC-BY-SA 3.0 -- se mostrares os dados, atribui a
    fonte ("Dados via Liquipedia, CC-BY-SA").
  - Reutiliza/cacheia os resultados; não repitas pedidos iguais.

Este módulo aplica um rate limiter simples (thread-safe) para nunca violar
o limite de 1 pedido/2s, independentemente de quantas vezes for chamado.
"""

from __future__ import annotations

import threading
import time

import requests

BASE_URL = "https://liquipedia.net/counterstrike/api.php"

# Termos de uso pedem um User-Agent identificável com contacto.
# Ajusta o e-mail/URL de contacto antes de uso em produção.
USER_AGENT = "CS2Pulse/1.0 (personal project; contact: set-your-contact-here)"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

_MIN_INTERVAL = 2.05  # segundos, com margem sobre o limite de 1/2s
_last_request_lock = threading.Lock()
_last_request_time = 0.0


def _rate_limited_get(params: dict) -> dict:
    global _last_request_time
    with _last_request_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        resp = SESSION.get(BASE_URL, params=params, timeout=15)
        _last_request_time = time.time()
    resp.raise_for_status()
    return resp.json()


def get_upcoming_matches(limit: int = 30) -> list[dict]:
    """
    Jogos CS2 próximos/em curso, via Cargo query (action=ask) sobre a
    categoria de resultados. Devolve lista de dicts com team1, team2,
    tournament, date (string), stream/liquipedia page.
    """
    query = (
        "[[Has team left::+]][[Has team right::+]][[Is upcoming match::true]]"
        f"|?Has team left|?Has team right|?Has tournament|?Has date"
        f"|?Has best of|?Has stream|limit={limit}|sort=Has date|order=asc"
    )
    data = _rate_limited_get({"action": "ask", "query": query, "format": "json"})
    results = data.get("query", {}).get("results", {})
    matches = []
    for page_data in results.values():
        po = page_data.get("printouts", {})
        matches.append({
            "team1": _first(po.get("Has team left")),
            "team2": _first(po.get("Has team right")),
            "tournament": _first(po.get("Has tournament")),
            "date": _first(po.get("Has date")),
            "best_of": _first(po.get("Has best of")),
            "stream": _first(po.get("Has stream")),
            "page": page_data.get("fulltext", ""),
        })
    return matches


def get_recent_results(limit: int = 30) -> list[dict]:
    """Resultados recentes (jogos já terminados)."""
    query = (
        "[[Has team left::+]][[Has team right::+]][[Is upcoming match::false]]"
        f"|?Has team left|?Has team right|?Has tournament|?Has date"
        f"|?Has winner|?Has score|limit={limit}|sort=Has date|order=desc"
    )
    data = _rate_limited_get({"action": "ask", "query": query, "format": "json"})
    results = data.get("query", {}).get("results", {})
    matches = []
    for page_data in results.values():
        po = page_data.get("printouts", {})
        matches.append({
            "team1": _first(po.get("Has team left")),
            "team2": _first(po.get("Has team right")),
            "tournament": _first(po.get("Has tournament")),
            "date": _first(po.get("Has date")),
            "winner": _first(po.get("Has winner")),
            "score": _first(po.get("Has score")),
            "page": page_data.get("fulltext", ""),
        })
    return matches


def _first(value) -> str:
    """Semantic MediaWiki devolve listas; extrai o primeiro valor legível."""
    if not value:
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        v = value[0]
        if isinstance(v, dict):
            return v.get("fulltext") or v.get("raw") or str(v)
        return str(v)
    return str(value)


ATTRIBUTION_NOTICE = "Dados de calendário via Liquipedia (CC-BY-SA 3.0)"

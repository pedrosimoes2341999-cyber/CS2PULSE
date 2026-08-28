"""
Fun88 / TF Gaming — movimento de odds para CS2.

API não-documentada, obtida por engenharia reversa (DevTools do browser). Não é uma API
pública — provavelmente viola os Termos de Serviço da Fun88, e os tokens abaixo podem parar
de funcionar a qualquer momento sem aviso.

Se isso acontecer, repete o processo de captura:
1. Abre uma página de jogo de CS2 na Fun88
2. F12 -> Network -> filtro Fetch/XHR
3. Clica no ícone de gráfico junto a uma linha de odds (abre o popup de histórico)
4. Encontra o pedido a "odds_history" -> botão direito -> Copy -> Copy as cURL
5. Atualiza as constantes abaixo com os novos valores desse cURL
"""

import time
import requests

FUN88_BASE = "https://api-v4.tf-api-rr3h.com/api/v8"
FUN88_GAME_ID = "1"  # "1" = CS:GO/CS2 na Fun88
FUN88_TIMEZONE = "Europe/Lisbon"

FUN88_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "th",
    "authorization": "Token 9278cadf62902610a21cfecfc60b8eeb2c830e93",
    "origin": "https://gc.tf-api-6oad.com",
    "public-token": "ea73cde978354c5daa4966a704272cc3",
    "referer": "https://gc.tf-api-6oad.com/",
    "tf-authorization": (
        "38f1abeff9a0d51deb1cb4e8376db46211f95911cd38bee1bba1b62dcbde197"
        "461401351fc615c1af115e5f35f6ed1d2b9d89673a576e528ba207c7d49a80f81"
    ),
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}


def fun88_events(game_id: str = FUN88_GAME_ID, timeout: int = 15) -> list[dict]:
    """Devolve a lista de jogos de hoje para o game_id dado (paginando se preciso)."""
    out: list[dict] = []
    page = 1
    while True:
        params = {
            "game_id": game_id,
            "outright": "false",
            "timing": "today",
            "market_option": "MATCH",
            "lang": "th",
            "timezone": FUN88_TIMEZONE,
            "combo": "false",
            "page": page,
        }
        r = requests.get(
            f"{FUN88_BASE}/events/",
            headers=FUN88_HEADERS,
            params=params,
            timeout=timeout,
        )
        r.raise_for_status()
        d = r.json()
        out.extend(d.get("results", []))
        if not d.get("next") or page > 20:
            break
        page += 1
    return out


def fun88_odds_history(market_id: int, timeout: int = 15) -> dict:
    """Devolve {opening_odds, odds_history[]} para um market_id (mercado 'vencedor')."""
    params = {
        "market_id": market_id,
        "combo": "false",
        "timezone": FUN88_TIMEZONE,
    }
    r = requests.get(
        f"{FUN88_BASE}/events/odds_history",
        headers=FUN88_HEADERS,
        params=params,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def implied_prob(home_euro: float, away_euro: float) -> float | None:
    """
    Probabilidade implícita normalizada (remove o overround/margem da casa),
    para comparar variações de forma justa mesmo entre jogos com odds de
    níveis muito diferentes.
    """
    if not home_euro or not away_euro or home_euro <= 0 or away_euro <= 0:
        return None
    raw_home = 1 / home_euro
    raw_away = 1 / away_euro
    total = raw_home + raw_away
    return raw_home / total if total > 0 else None


def fun88_odds_series(market_id: int, timeout: int = 15) -> list[dict]:
    """
    Devolve a evolução cronológica (mais antiga primeiro) das odds de um
    mercado: [{datetime, home_euro, away_euro, home_prob, away_prob}, ...]

    A API devolve odds_history com o mais recente primeiro, e o último
    elemento duplica a opening_odds -- invertendo a lista ficamos com a
    ordem cronológica certa, já incluindo o ponto de abertura como
    primeiro elemento (sem precisarmos de o juntar à parte).
    """
    hist = fun88_odds_history(market_id, timeout=timeout)
    history = hist.get("odds_history") or []
    points = list(reversed(history))

    series = []
    for p in points:
        home_euro = (p.get("home_odds") or {}).get("euro_odds")
        away_euro = (p.get("away_odds") or {}).get("euro_odds")
        home_prob = implied_prob(home_euro, away_euro)
        series.append({
            "datetime": p.get("datetime"),
            "home_euro": home_euro,
            "away_euro": away_euro,
            "home_prob": home_prob,
            "away_prob": (1 - home_prob) if home_prob is not None else None,
        })
    return series


def fetch_odds_movement(game_id: str = FUN88_GAME_ID) -> list[dict]:
    """
    Vai buscar todos os jogos de hoje e o respetivo movimento de odds,
    devolvendo uma lista ordenada pela maior variação absoluta (em pontos
    percentuais de probabilidade implícita da equipa da casa).

    Cada item: {
        event_id, competition_name, start_datetime, home_team, away_team,
        best_of, in_play, opening_home_euro, opening_away_euro,
        current_home_euro, current_away_euro, opening_home_prob,
        current_home_prob, prob_swing_pp
    }
    """
    events = fun88_events(game_id)
    rows: list[dict] = []

    for ev in events:
        markets = ev.get("markets") or []
        if not markets:
            continue
        market_id = markets[0].get("market_id")
        if not market_id:
            continue

        try:
            hist = fun88_odds_history(market_id)
        except requests.RequestException:
            continue

        opening = hist.get("opening_odds")
        history = hist.get("odds_history") or []
        latest = history[0] if history else opening
        if not opening or not latest:
            continue

        opening_home_euro = (opening.get("home_odds") or {}).get("euro_odds")
        opening_away_euro = (opening.get("away_odds") or {}).get("euro_odds")
        current_home_euro = (latest.get("home_odds") or {}).get("euro_odds")
        current_away_euro = (latest.get("away_odds") or {}).get("euro_odds")

        opening_prob = implied_prob(opening_home_euro, opening_away_euro)
        current_prob = implied_prob(current_home_euro, current_away_euro)
        if opening_prob is None or current_prob is None:
            continue

        swing_pp = round((current_prob - opening_prob) * 1000) / 10

        rows.append({
            "event_id": ev.get("event_id"),
            "market_id": market_id,
            "competition_name": ev.get("competition_name"),
            "start_datetime": ev.get("start_datetime"),
            "home_team": (ev.get("home") or {}).get("team_name"),
            "away_team": (ev.get("away") or {}).get("team_name"),
            "best_of": ev.get("best_of"),
            "in_play": ev.get("in_play"),
            "opening_home_euro": opening_home_euro,
            "opening_away_euro": opening_away_euro,
            "current_home_euro": current_home_euro,
            "current_away_euro": current_away_euro,
            "opening_home_prob": opening_prob,
            "current_home_prob": current_prob,
            "prob_swing_pp": swing_pp,
        })

    rows.sort(key=lambda r: abs(r["prob_swing_pp"]), reverse=True)
    return rows


if __name__ == "__main__":
    # Teste rápido standalone: python fun88_odds.py
    t0 = time.time()
    data = fetch_odds_movement()
    print(f"{len(data)} jogos processados em {time.time()-t0:.1f}s\n")
    for row in data[:10]:
        print(
            f"{row['prob_swing_pp']:+.1f}pp  "
            f"{row['home_team']} vs {row['away_team']}  "
            f"({row['opening_home_euro']}->{row['current_home_euro']} / "
            f"{row['opening_away_euro']}->{row['current_away_euro']})"
        )

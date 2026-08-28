"""
polymarket_engine.py
=====================
Motor de deteção de combos CS2 no Polymarket, via sinal oficial `isCombo`
da Data API. Reaproveita e organiza a lógica do script CLI original como
funções importáveis, com callbacks de progresso em vez de prints -- para
poderem ser usadas tanto em CLI como em Streamlit (barras de progresso).

APIs usadas (todas públicas, sem API key):
    Gamma API   https://gamma-api.polymarket.com
    Data API    https://data-api.polymarket.com
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

_VS_PATTERN = re.compile(r"\bvs\b", re.IGNORECASE)


def _resolve_leg_outcome(leg: dict, known_outcomes: list[str] | None = None) -> str:
    """
    Tenta identificar a OPÇÃO escolhida numa perna da combo (ex: "Lavked",
    não só o mercado "Lavked vs Vexar"). O schema exato da API de combos
    para isto não está documentado publicamente e não devolve o nome
    (confirmado -- só o índice), por isso aceita opcionalmente uma lista
    `known_outcomes` vinda do Gamma (que sabemos ter os nomes reais) para
    resolver com certeza a perna do jogo em análise.
    """
    idx = leg.get("leg_outcome_index")
    if idx is None:
        idx = leg.get("outcome_index")
    if idx is None:
        idx = leg.get("outcomeIndex")

    if known_outcomes is not None and idx is not None:
        try:
            return str(known_outcomes[int(idx)])
        except Exception:
            pass

    direct = leg.get("outcome") or leg.get("leg_outcome") or leg.get("outcomeName")
    if direct:
        return str(direct)

    market_info = leg.get("market", {}) or {}
    outcomes_raw = market_info.get("outcomes")
    if outcomes_raw is not None and idx is not None:
        try:
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
            return str(outcomes[int(idx)])
        except Exception:
            pass

    if idx is not None:
        return f"outcome #{idx}"
    return "?"


def _leg_title(leg: dict) -> str:
    market_info = leg.get("market", {}) or {}
    return market_info.get("title") or market_info.get("question") or "?"


def _parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None

GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "cs2-pulse-app/1.0"})

REQUEST_TIMEOUT = 12
RATE_LIMIT_SLEEP = 0.02  # a lógica de retry-on-429 já cobre picos, isto pode ser baixo
DEFAULT_WORKERS = 12

ProgressFn = Optional[Callable[[str], None]]


def _noop(msg: str) -> None:
    pass


def _get(url: str, params: dict | None = None) -> dict | list:
    for attempt in range(4):
        resp = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            time.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        time.sleep(RATE_LIMIT_SLEEP)
        return resp.json()
    raise RuntimeError(f"Falhou depois de várias tentativas: {url}")


# ---------------------------------------------------------------------------
# Descoberta de jogos/mercados
# ---------------------------------------------------------------------------

def find_cs2_events(limit: int = 100, closed: bool = False) -> list[dict]:
    params = {
        "tag_slug": "cs2",
        "active": "true",
        "closed": str(closed).lower(),
        "limit": limit,
        "order": "volume",
        "ascending": "false",
    }
    data = _get(f"{GAMMA_BASE}/events", params=params)
    return data if isinstance(data, list) else data.get("events", [])


_OTHER_GAME_KEYWORDS = [
    "league of legends", "dota", "valorant", "mobile legends", "overwatch",
    "rainbow six", "call of duty", "rocket league", "starcraft",
    "honor of kings", "epl-", "premier league", "champions league",
    "la liga", "serie a", "bundesliga", "ligue 1", "nba", "nfl", "nhl",
    "mlb", "wnba", "tennis", "golf", "nascar", "mma", "cricket",
]


def get_all_tags() -> list[dict]:
    data = _get(f"{GAMMA_BASE}/tags")
    return data if isinstance(data, list) else data.get("tags", [])


def get_tag_by_slug(slug: str) -> dict | None:
    """Busca direta de UMA tag pelo slug -- evita ter de paginar /tags
    inteiro (que só devolve as primeiras ~50 de cada vez)."""
    try:
        data = _get(f"{GAMMA_BASE}/tags/slug/{slug}")
        if isinstance(data, dict) and data.get("id"):
            return data
    except Exception:
        pass
    return None


def get_sports() -> list[dict]:
    data = _get(f"{GAMMA_BASE}/sports")
    return data if isinstance(data, list) else data.get("sports", [])


def get_cs2_primary_tag_id() -> tuple[str | None, dict]:
    """
    Descoberta CONFIRMADA com dados reais: a tag `cs2` (id 100677) é uma
    tag genérica só usada em props/futures -- os jogos de verdade estão
    etiquetados com a tag `counter-strike-2` (id 100780), que é a
    `primaryTagId` do objeto `sport` (sport=='cs2') devolvido por /sports.

    Vai buscar isto dinamicamente via /sports em vez de fixar o id 100780,
    para continuar a funcionar mesmo que o id mude no futuro. Se /sports
    falhar por algum motivo, cai para a tag "counter-strike-2" por slug.
    """
    debug: dict = {}
    try:
        sports = get_sports()
        debug["n_sports"] = len(sports)
        cs2_sport = next((s for s in sports if (s.get("sport") or "").lower() == "cs2"), None)
        if cs2_sport:
            debug["cs2_sport_raw"] = cs2_sport
            tag_id = cs2_sport.get("primaryTagId")
            if tag_id:
                return str(tag_id), debug
        debug["aviso"] = "não encontrei sport=='cs2' em /sports, ou sem primaryTagId"
    except Exception as e:
        debug["erro_sports"] = str(e)

    # fallback: tag por slug direto, confirmado nos dados reais do evento
    tag = get_tag_by_slug("counter-strike-2")
    if tag:
        debug["fallback_tag_por_slug"] = tag
        return str(tag.get("id")), debug
    debug["erro_fallback"] = "também não encontrei a tag 'counter-strike-2' por slug"
    return None, debug


def get_events_by_tag_id(tag_id: str, max_pages: int = 10, page_size: int = 100) -> list[dict]:
    """
    Pagina TODOS os eventos de uma tag por tag_id (numérico) -- mais fiável
    do que tag_slug, que nalguns casos só devolve um subconjunto fixo.
    """
    all_events: list[dict] = []
    offset = 0
    for _ in range(max_pages):
        data = _get(f"{GAMMA_BASE}/events", params={
            "tag_id": tag_id, "active": "true", "closed": "false",
            "limit": page_size, "offset": offset,
        })
        events = data if isinstance(data, list) else data.get("events", [])
        if not events:
            break
        all_events.extend(events)
        if len(events) < page_size:
            break
        offset += page_size
    return all_events


def discover_cs2_subtags(all_tags: list[dict] | None = None) -> tuple[list[str], dict]:
    """
    Descobre dinamicamente as sub-tags de torneios de CS2 (ex: blast-open,
    cct-europe, moscow-cyber-games) a partir da relação pai-filho em /tags,
    em vez de depender de uma lista fixa que fica desatualizada.

    Devolve (lista_de_slugs, debug_info) -- debug_info mostra a estrutura
    real de um tag (chaves disponíveis) para diagnóstico, caso os nomes de
    campo usados aqui (parentTag/parentTagId/...) não batam certo.
    """
    all_tags = all_tags if all_tags is not None else get_all_tags()
    debug: dict = {"n_tags_total": len(all_tags)}

    cs2_tag = next((t for t in all_tags if (t.get("slug") or "").lower() == "cs2"), None)
    if not cs2_tag:
        debug["erro"] = "não encontrei nenhuma tag com slug == 'cs2' em /tags"
        return [], debug

    debug["cs2_tag_raw"] = cs2_tag  # mostra TODOS os campos, para vermos o schema real
    cs2_id = cs2_tag.get("id")

    candidate_parent_fields = ["parentTag", "parentTagId", "parentId", "parent_tag_id", "parentTagID"]
    children: list[str] = []
    field_used = None
    for field_name in candidate_parent_fields:
        found = [t for t in all_tags if str(t.get(field_name)) == str(cs2_id) and t.get(field_name) is not None]
        if found:
            children = [t.get("slug") for t in found if t.get("slug")]
            field_used = field_name
            break

    debug["campo_parent_usado"] = field_used
    debug["n_subtags_encontradas"] = len(children)
    debug["subtags"] = children
    return children, debug


def find_cs2_matches(limit: int = 30, scan: int = 200, debug: bool = False):
    """
    Filtra, de entre os eventos CS2, apenas os JOGOS (confrontos diretos
    equipa A vs equipa B) -- exclui mercados de futures/props do torneio.

    DESCOBERTA IMPORTANTE (confirmada com dados reais): a tag `cs2` no
    Polymarket corresponde só à página de futures/props do jogo -- os
    jogos individuais estão etiquetados por TORNEIO (ex: `moscow-cyber-games`,
    `cct-europe`), não pela tag genérica `cs2`.

    Estratégia (por ordem, para no primeiro que encontrar resultados):
      1. Descobrir dinamicamente as sub-tags de torneio via /tags (relação
         pai-filho com a tag `cs2`) e juntar os eventos de cada uma.
      2. Full-text search "vs" filtrado à tag cs2, via /public-search --
         usado como fallback, com filtro extra para excluir outros jogos
         que a pesquisa de texto livre possa trazer por engano.

    Se debug=True, devolve (matches, debug_info) com o detalhe de cada
    tentativa, para diagnóstico.
    """
    debug_info: dict = {"attempts": []}
    seen_slugs: set[str] = set()
    unique_matches: list[dict] = []

    def _has_vs(e: dict) -> bool:
        return bool(_VS_PATTERN.search(e.get("title") or ""))

    def _mentions_other_game(e: dict) -> bool:
        title = (e.get("title") or "").lower()
        return any(kw in title for kw in _OTHER_GAME_KEYWORDS)

    def _add(events: list[dict], check_other_game: bool = False):
        for e in events:
            if not _has_vs(e):
                continue
            if check_other_game and _mentions_other_game(e):
                continue
            slug = e.get("slug")
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                unique_matches.append(e)

    # Tentativa 1: tag correta dos jogos -- `counter-strike-2` (não `cs2`,
    # que é uma tag genérica só de props/futures). Descoberta dinamicamente
    # via /sports (sport=='cs2' -> primaryTagId), confirmado com dados reais.
    try:
        tag_id, tag_debug = get_cs2_primary_tag_id()
        if tag_id:
            events = get_events_by_tag_id(tag_id, max_pages=10, page_size=100)
            debug_info["attempts"].append({
                "method": f"get_events_by_tag_id (tag_id={tag_id}, via /sports primaryTagId)",
                "n_events": len(events),
                "sample_titles": [e.get("title", "") for e in events[:10]],
                "tag_discovery": tag_debug,
            })
            _add(events, check_other_game=False)
        else:
            debug_info["attempts"].append({
                "method": "get_cs2_primary_tag_id",
                "error": "não consegui descobrir o tag_id correto",
                "tag_discovery": tag_debug,
            })
    except Exception as e:
        debug_info["attempts"].append({"method": "get_events_by_tag_id", "error": str(e)})

    # Tentativa 2 (fallback): descoberta dinâmica de sub-tags de torneio via /tags
    if not unique_matches:
        try:
            subtags, subtag_debug = discover_cs2_subtags()
            per_tag_results = {}
            for tag in subtags:
                try:
                    data = _get(f"{GAMMA_BASE}/events", params={
                        "tag_slug": tag, "active": "true", "closed": "false", "limit": 50,
                    })
                    events = data if isinstance(data, list) else data.get("events", [])
                    per_tag_results[tag] = len(events)
                    _add(events, check_other_game=False)
                except Exception:
                    per_tag_results[tag] = "erro"
            debug_info["attempts"].append({
                "method": "descoberta dinâmica de sub-tags via /tags",
                "subtag_discovery": subtag_debug,
                "resultados_por_tag": per_tag_results,
            })
        except Exception as e:
            debug_info["attempts"].append({"method": "descoberta dinâmica de sub-tags", "error": str(e)})

    # Tentativa 3 (fallback final): full-text search por "vs" filtrado à tag cs2
    if not unique_matches:
        try:
            data = _get(f"{GAMMA_BASE}/public-search", params={
                "q": "vs", "events_tag": "cs2", "limit_per_type": scan,
            })
            events = data.get("events") or []
            debug_info["attempts"].append({
                "method": "public-search q=vs events_tag=cs2",
                "n_events": len(events),
                "sample_titles": [e.get("title", "") for e in events[:10]],
            })
            _add(events, check_other_game=True)
        except Exception as e:
            debug_info["attempts"].append({"method": "public-search", "error": str(e)})

    # Filtrar jogos já terminados. DESCOBERTA (confirmada com dados reais):
    # `startDate`/`endDate` são sobre a JANELA DO MERCADO (quando abre para
    # negociar / prazo limite de resolução), não a hora real do jogo -- por
    # isso ordenar por essas datas trazia jogos de há duas semanas para o
    # topo (o mercado deles simplesmente ainda não fechou). A hora real do
    # jogo está em `startTime`, e o evento já vem com flags diretas `ended`
    # (terminado) e `live` (a decorrer agora) -- usar essas em vez de datas.
    def _match_start(e: dict):
        return _parse_iso(e.get("startTime")) or _parse_iso(e.get("startDate"))

    now = datetime.now(timezone.utc)

    def _is_current(e: dict) -> bool:
        if e.get("ended") is True:
            return False
        if e.get("live"):
            return True
        start = _match_start(e)
        if start is None:
            return True  # sem data -- não excluir por segurança
        # janela razoável: até 24h no passado (jogos longos/atrasados a
        # resolver) e até 21 dias no futuro (evita jogos "presos" há
        # semanas sem resolução, que não são realmente "atuais")
        return (now - start) <= timedelta(hours=24) and (start - now) <= timedelta(days=21)

    n_before_date_filter = len(unique_matches)
    unique_matches = [e for e in unique_matches if _is_current(e)]
    debug_info["n_excluidos_por_data_ou_ended"] = n_before_date_filter - len(unique_matches)

    # Ordenar: jogos "live" primeiro, depois por hora real de início
    # (startTime) -- fallback para startDate só se startTime não existir.
    def _sort_key(e: dict):
        start = _match_start(e) or datetime.max.replace(tzinfo=timezone.utc)
        return (not e.get("live", False), start)

    unique_matches.sort(key=_sort_key)
    result = unique_matches[:limit]

    if debug:
        return result, debug_info
    return result


def markets_for_event(event: dict) -> list[dict]:
    return event.get("markets", [])


def extract_slug_from_url(url_or_slug: str) -> str:
    text = url_or_slug.strip()
    if "://" not in text and "polymarket.com" not in text:
        return text
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        raise ValueError(f"Não consegui extrair um slug do URL: {url_or_slug}")
    slug = segments[-1]
    for suffix in ("-more-markets",):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
    return slug


def get_event_by_slug(slug: str) -> dict | None:
    data = _get(f"{GAMMA_BASE}/events", params={"slug": slug})
    events = data if isinstance(data, list) else data.get("events", [])
    return events[0] if events else None


def get_market_by_slug(slug: str) -> dict | None:
    data = _get(f"{GAMMA_BASE}/markets", params={"slug": slug})
    markets = data if isinstance(data, list) else data.get("markets", [])
    return markets[0] if markets else None


def resolve_event(slug: str) -> dict | None:
    event = get_event_by_slug(slug)
    if event:
        return event
    market = get_market_by_slug(slug)
    if not market:
        return None
    parent_slug = market.get("eventSlug")
    if parent_slug and parent_slug != slug:
        parent_event = get_event_by_slug(parent_slug)
        if parent_event:
            return parent_event
    return {"title": market.get("question", slug), "slug": slug, "markets": [market]}


def resolve_username_to_wallet(username: str) -> str | None:
    q = username.lstrip("@").strip()
    data = _get(
        f"{GAMMA_BASE}/public-search",
        params={"q": q, "search_profiles": "true", "limit_per_type": 5},
    )
    profiles = data.get("profiles") or []
    if not profiles:
        return None
    for p in profiles:
        if (p.get("pseudonym") or "").lower() == q.lower() or (p.get("name") or "").lower() == q.lower():
            return p.get("proxyWallet")
    return profiles[0].get("proxyWallet")


def resolve_wallet_input(text: str) -> str | None:
    raw = text.strip()
    if raw.lower().startswith("0x") and len(raw) == 42:
        return raw
    candidate = raw
    if "://" in raw or "polymarket.com" in raw:
        if "://" not in raw:
            raw = "https://" + raw
        segments = [s for s in urlparse(raw).path.split("/") if s]
        candidate = segments[-1] if segments else raw
    if candidate.lower().startswith("0x") and len(candidate) == 42:
        return candidate
    return resolve_username_to_wallet(candidate)


# ---------------------------------------------------------------------------
# Descoberta de wallets por mercado
# ---------------------------------------------------------------------------

def discover_wallets_for_market(condition_id: str, max_pages: int = 30, label: str = "",
                                 on_progress: ProgressFn = None) -> set[str]:
    on_progress = on_progress or _noop
    wallets: set[str] = set()
    n_trades = 0

    offset = 0
    page_size = 500
    for _ in range(max_pages):
        params = {"market": condition_id, "limit": page_size, "offset": offset}
        try:
            data = _get(f"{DATA_BASE}/trades", params=params)
        except Exception as e:
            on_progress(f"{label} [aviso] falha a obter trades: {e}")
            break
        if not data:
            break
        n_trades += len(data)
        for row in data:
            w = row.get("proxyWallet")
            if w:
                wallets.add(w)
        if len(data) < page_size:
            break
        offset += page_size

    n_from_trades = len(wallets)
    try:
        holders_data = _get(f"{DATA_BASE}/holders", params={"market": condition_id, "limit": 100})
        for token_entry in (holders_data or []):
            for h in (token_entry.get("holders") or []):
                w = h.get("proxyWallet")
                if w:
                    wallets.add(w)
    except Exception as e:
        on_progress(f"{label} [aviso] falha a obter holders, a continuar sem eles: {e}")

    on_progress(f"{label} {n_trades} trades, {n_from_trades} wallets por trades "
                f"+ {len(wallets) - n_from_trades} por holders = {len(wallets)} wallets")
    return wallets


# ---------------------------------------------------------------------------
# Deteção de combos via /activity (isCombo=true)
# ---------------------------------------------------------------------------

def get_activity(user: str, market_ids: list[str] | None = None, type_: str = "TRADE",
                  max_pages: int = 10, start: int | None = None) -> list[dict]:
    """
    IMPORTANTE: uma linha isCombo=true tem `conditionId` = ID da COMBO, não
    do mercado-perna. Para apanhar combos, chamar SEM market_ids.

    `start` (opcional): timestamp epoch a partir do qual procurar -- reduz
    o volume de dados pedidos para wallets muito ativas, à custa de poder
    perder combos feitas antes dessa data (usar com cuidado).
    """
    rows: list[dict] = []
    offset = 0
    page_size = 500
    for _ in range(max_pages):
        params = {"user": user, "type": type_, "limit": page_size, "offset": offset}
        if market_ids:
            params["market"] = ",".join(market_ids)
        if start is not None:
            params["start"] = start
        data = _get(f"{DATA_BASE}/activity", params=params)
        if not data:
            break
        rows.extend(data)
        if len(data) < page_size:
            break
        offset += page_size
    return rows


def get_combo_activity(user: str, combo_condition_id: str) -> list[dict]:
    data = _get(
        f"{DATA_BASE}/v1/activity/combos",
        params={"user": user, "market_id": combo_condition_id, "limit": 50},
    )
    if isinstance(data, dict):
        return data.get("activity", [])
    return data or []


@dataclass
class ComboRow:
    jogo: str
    wallet: str
    combo_condition_id: str
    tx_hash: str
    timestamp: int
    n_pernas_totais: int
    n_pernas_neste_jogo: int
    amount_usdc: float
    condition_id_perna: str
    perna_titulo: str
    opcao_escolhida: str
    outras_pernas_da_combo: str
    wallet_e_holder_do_jogo: bool


def find_combos_for_wallet(
    wallet: str, event_title: str, condition_ids: list[str], debug_cb: ProgressFn = None,
    activity_start_ts: int | None = None, outcomes_by_condition_id: dict[str, list[str]] | None = None,
) -> list[ComboRow]:
    """
    activity_start_ts (opcional): restringe a consulta de atividade a partir
    desta data (epoch) -- acelera para wallets muito ativas, mas pode
    perder combos feitas antes dessa data. Usar só quando a velocidade
    importa mais do que a garantia de completude total.
    outcomes_by_condition_id (opcional): mapa condition_id -> lista de
    nomes de outcomes (ex: ["Lavked", "Vexar"]), vindo do Gamma -- usado
    para resolver com certeza a opção escolhida nas pernas DESTE jogo,
    já que a API de combos não devolve o nome, só o índice.
    """
    debug_cb = debug_cb or _noop
    outcomes_by_condition_id = outcomes_by_condition_id or {}
    all_rows = get_activity(wallet, market_ids=None, type_="TRADE", start=activity_start_ts)

    non_combo_condition_ids = {
        r["conditionId"] for r in all_rows
        if not r.get("isCombo") and r["conditionId"] in condition_ids
    }

    combo_rows_raw = [r for r in all_rows if r.get("isCombo")]
    if not combo_rows_raw:
        return []

    seen_combo_ids: set[str] = set()
    out: list[ComboRow] = []
    for r in combo_rows_raw:
        combo_condition_id = r["conditionId"]
        if combo_condition_id in seen_combo_ids:
            continue
        seen_combo_ids.add(combo_condition_id)

        details = get_combo_activity(wallet, combo_condition_id)
        for d in details:
            legs = d.get("legs", [])
            legs_neste_jogo = [leg for leg in legs if leg.get("leg_condition_id") in condition_ids]
            if not legs_neste_jogo:
                continue
            is_holder = any(leg["leg_condition_id"] in non_combo_condition_ids for leg in legs_neste_jogo)
            for leg in legs_neste_jogo:
                outras_pernas = [
                    f"{_leg_title(other)} — "
                    f"{_resolve_leg_outcome(other, outcomes_by_condition_id.get(other.get('leg_condition_id')))}"
                    for other in legs
                    if other.get("leg_condition_id") != leg.get("leg_condition_id")
                ]
                out.append(
                    ComboRow(
                        jogo=event_title,
                        wallet=wallet,
                        combo_condition_id=combo_condition_id,
                        tx_hash=d.get("tx_hash", ""),
                        timestamp=d.get("timestamp", 0),
                        n_pernas_totais=len(legs),
                        n_pernas_neste_jogo=len(legs_neste_jogo),
                        amount_usdc=float(d.get("amount_usdc") or 0),
                        condition_id_perna=leg.get("leg_condition_id", ""),
                        perna_titulo=_leg_title(leg),
                        opcao_escolhida=_resolve_leg_outcome(
                            leg, outcomes_by_condition_id.get(leg.get("leg_condition_id"))),
                        outras_pernas_da_combo="; ".join(outras_pernas) if outras_pernas else "(perna única)",
                        wallet_e_holder_do_jogo=is_holder,
                    )
                )
        if out:
            tag = "HOLDER" if out[-1].wallet_e_holder_do_jogo else "SÓ-COMBO"
            debug_cb(f"combo {combo_condition_id[:12]}... wallet={wallet[:10]}... [{tag}] "
                     f"valor≈${out[-1].amount_usdc:.2f}")
    return out


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------

def analyze_event(event: dict, workers: int = DEFAULT_WORKERS,
                   on_progress: ProgressFn = None,
                   on_stage: Optional[Callable[[str, float], None]] = None,
                   extra_wallets: list[str] | None = None,
                   activity_start_ts: int | None = None,
                   skip_wallets: set[str] | None = None,
                   on_wallet_checked: Optional[Callable[[str, bool], None]] = None) -> list[ComboRow]:
    """
    on_progress(msg): linhas de log (para uma área de texto/expander)
    on_stage(label, fraction 0-1): para uma barra de progresso
    extra_wallets: wallets a verificar SEMPRE, além das descobertas
        automaticamente (ex: vindas da watchlist) -- útil porque a API
        pública do Polymarket não tem forma de listar TODOS os
        participantes de um mercado (só top holders + trades normais), por
        isso uma wallet que só entrou via combo com posição pequena pode
        escapar à descoberta automática.
    activity_start_ts: opcional, restringe a verificação de atividade de
        cada wallet a partir desta data (epoch) -- acelera bastante quando
        há muitas wallets candidatas (ex: modo exaustivo on-chain), à custa
        de poder perder combos feitas antes dessa data.
    skip_wallets: wallets a NÃO verificar (já verificadas recentemente para
        este jogo, sem combo encontrada) -- acelera re-análises do mesmo
        jogo. Wallets com combo encontrada nunca devem entrar aqui (só
        cachear negativos, para nunca mostrar dados desatualizados).
    on_wallet_checked(wallet, encontrou_combo): chamado depois de cada
        wallet ser verificada, para a app poder gravar em cache.
    """
    on_progress = on_progress or _noop
    on_stage = on_stage or (lambda label, frac: None)

    title = event.get("title", event.get("slug", "?"))
    markets = markets_for_event(event)
    condition_ids = [m["conditionId"] for m in markets if m.get("conditionId")]
    if not condition_ids:
        on_progress("(sem mercados encontrados)")
        return []

    # nomes reais dos outcomes de cada mercado deste jogo (vindos do Gamma,
    # que sabemos ter os nomes corretos) -- usados para resolver a "opção
    # escolhida" em cada perna com certeza, já que a API de combos só
    # devolve o índice, não o nome
    outcomes_by_condition_id: dict[str, list[str]] = {}
    for mkt in markets:
        cid = mkt.get("conditionId")
        outcomes_raw = mkt.get("outcomes")
        if cid and outcomes_raw:
            try:
                outcomes_by_condition_id[cid] = (
                    json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                )
            except Exception:
                pass

    on_stage(f"A descobrir wallets em {len(condition_ids)} mercados...", 0.05)
    all_wallets: set[str] = set()
    n_market_workers = min(workers, len(condition_ids)) or 1
    with ThreadPoolExecutor(max_workers=n_market_workers) as ex:
        futures = {
            ex.submit(discover_wallets_for_market, cid, label=f"[{i}/{len(condition_ids)}]",
                      on_progress=on_progress): cid
            for i, cid in enumerate(condition_ids, start=1)
        }
        for fut in as_completed(futures):
            try:
                all_wallets |= fut.result()
            except Exception as e:
                on_progress(f"[aviso] falha num mercado: {e}")

    if extra_wallets:
        n_extra_novas = len(set(extra_wallets) - all_wallets)
        all_wallets |= set(extra_wallets)
        on_progress(f"+{n_extra_novas} wallets da watchlist adicionadas (sempre verificadas)")

    if skip_wallets:
        n_before_cache = len(all_wallets)
        all_wallets -= skip_wallets
        n_skipped = n_before_cache - len(all_wallets)
        if n_skipped:
            on_progress(f"-{n_skipped} wallets saltadas (já verificadas recentemente, sem combo)")

    on_progress(f"Total: {len(all_wallets)} wallets únicas")
    on_stage(f"A verificar combos em {len(all_wallets)} wallets...", 0.2)

    rows: list[ComboRow] = []
    done = 0
    lock = threading.Lock()
    n_wallets = len(all_wallets) or 1

    def _check(wallet: str) -> list[ComboRow]:
        result = find_combos_for_wallet(wallet, title, condition_ids, debug_cb=on_progress,
                                         activity_start_ts=activity_start_ts,
                                         outcomes_by_condition_id=outcomes_by_condition_id)
        if on_wallet_checked:
            try:
                on_wallet_checked(wallet, bool(result))
            except Exception:
                pass
        return result

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_check, w): w for w in all_wallets}
        for fut in as_completed(futures):
            wallet = futures[fut]
            with lock:
                done += 1
                frac = 0.2 + 0.8 * (done / n_wallets)
                on_stage(f"A verificar wallets ({done}/{n_wallets})...", frac)
            try:
                rows.extend(fut.result())
            except Exception as e:
                on_progress(f"[aviso] falha na wallet {wallet[:10]}...: {e}")

    on_stage("Concluído", 1.0)
    on_progress(f"Combos encontradas: {len({r.combo_condition_id for r in rows})}")
    return rows


def rows_to_dicts(rows: list[ComboRow]) -> list[dict]:
    return [
        {
            "jogo": r.jogo,
            "wallet": r.wallet,
            "combo_condition_id": r.combo_condition_id,
            "tx_hash": r.tx_hash,
            "timestamp": r.timestamp,
            "n_pernas_totais": r.n_pernas_totais,
            "n_pernas_neste_jogo": r.n_pernas_neste_jogo,
            "valor_investido_usdc": round(r.amount_usdc, 2),
            "perna_condition_id": r.condition_id_perna,
            "perna_titulo": r.perna_titulo,
            "opcao_escolhida": r.opcao_escolhida,
            "outras_pernas_da_combo": r.outras_pernas_da_combo,
            "wallet_e_holder_do_jogo": r.wallet_e_holder_do_jogo,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Leaderboard (para o feed de "smart money")
# ---------------------------------------------------------------------------

def get_leaderboard(category: str = "OVERALL", time_period: str = "DAY", limit: int = 20) -> list[dict]:
    """
    category: OVERALL, POLITICS, SPORTS, ESPORTS, CRYPTO, CULTURE, MENTIONS,
              WEATHER, ECONOMICS, TECH, FINANCE
    time_period: DAY, WEEK, MONTH, ALL

    NOTA: não existe uma categoria específica para CS2 -- ESPORTS inclui
    todos os jogos (LoL, Dota, Valorant, etc.). Para filtrar só CS2, usa
    filter_leaderboard_by_cs2_activity() depois de obter este leaderboard.
    """
    data = _get(f"{DATA_BASE}/v1/leaderboard",
                params={"category": category, "timePeriod": time_period, "limit": limit})
    return data if isinstance(data, list) else data.get("leaderboard", [])


def filter_leaderboard_by_cs2_activity(
    leaderboard: list[dict], cs2_condition_ids: list[str],
    on_progress: ProgressFn = None, workers: int = 15,
) -> list[dict]:
    """
    Cruza um leaderboard (ex: categoria ESPORTS) com atividade real em
    mercados CS2, para chegar a uma lista só de traders que de facto
    negoceiam CS2. Uma chamada /activity por trader, em paralelo.
    """
    on_progress = on_progress or _noop

    def _check(entry: dict) -> dict | None:
        wallet = entry.get("proxyWallet") or entry.get("wallet")
        if not wallet:
            return None
        rows = get_activity(wallet, market_ids=cs2_condition_ids, type_="TRADE")
        on_progress(f"  {entry.get('userName', wallet[:10])}: "
                    f"{len(rows)} trades CS2 encontrados")
        if rows:
            entry = dict(entry)
            entry["cs2_trades_encontrados"] = len(rows)
            return entry
        return None

    result: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_check, entry) for entry in leaderboard]
        for fut in as_completed(futures):
            r = fut.result()
            if r is not None:
                result.append(r)

    # ordenar pelo mesmo critério do leaderboard original (rank), já que a
    # ordem pode ter ficado embaralhada pelo paralelismo
    def _rank_key(e: dict):
        r = e.get("rank", "")
        return int(r) if str(r).isdigit() else 10**9

    result.sort(key=_rank_key)
    return result

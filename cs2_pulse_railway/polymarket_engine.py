"""
onchain_discovery.py
======================
Descoberta EXAUSTIVA de wallets que tocaram numa posição (mercado CS2),
lendo diretamente os eventos de transferência ERC1155 do contrato CTF
(Conditional Tokens Framework) do Polymarket na Polygon.

PORQUÊ ISTO EXISTE
------------------
A API pública do Polymarket não tem nenhum endpoint que liste TODOS os
participantes de um mercado -- só /trades (trades normais) e /holders
(top ~20-100 por token). Uma wallet que só entrou via combo com uma
posição pequena pode escapar às duas. A única fonte verdadeiramente
exaustiva é a própria blockchain: cada posição (Yes/No de um mercado) é
um token ERC1155 com um `positionId` fixo, e todo movimento desse token
(mint, transfer, merge) fica registado num evento `TransferSingle` ou
`TransferBatch` do contrato CTF.

Isto é mais lento e pesado que a descoberta normal (pode envolver
milhares de eventos on-chain), e por isso é opcional -- só corre quando
ativado explicitamente.

REQUISITOS
----------
Precisa de uma API key gratuita do Polygonscan (https://polygonscan.com/apis
-- "Sign Up", depois "API Keys" no menu da conta; é imediato, sem espera).
Sem key, o Polygonscan aceita pedidos mas com um limite muito baixo
(1 pedido/5s); com key gratuita, sobe para 5 pedidos/s.

LIMITAÇÕES CONHECIDAS (por serem honesto sobre o que este código faz):
  - TransferBatch (usado quando várias posições se movem na mesma
    transação) só é decodificado para o caso comum de 1-4 posições; casos
    mais complexos podem ser ignorados silenciosamente (contabilizados em
    `n_transfer_batch_ignorados` no resultado de debug).
  - O intervalo de blocos é aproximado a partir de timestamps (a Polygon
    não tem uma relação perfeitamente linear bloco->tempo).
  - Nunca testado contra a API ao vivo neste ambiente (sem rede) -- corre
    primeiro com poucas horas de intervalo para validar antes de confiares.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

POLYGONSCAN_BASE = "https://api.etherscan.io/v2/api"
POLYGON_CHAIN_ID = "137"
CTF_CONTRACT = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"

# Contrato que liquida as COMBOS de facto (confirmado com uma transação real
# fornecida pelo utilizador + confirmado de forma independente por outro
# projeto que mapeou os endereços da arquitetura nova do Polymarket, 2026).
# As combos NÃO transferem os tokens das pernas nem passam pelo CTF/
# PositionManager de forma que bata certo com os positionIds -- passam por
# aqui, com um evento próprio que tem as duas wallets da negociação
# (requester + market maker da perna) como parâmetros indexados.
COMBOS_EXCHANGE = "0xe3333700ca9d93003f00f0f71f8515005f6c00aa"
COMBO_SETTLEMENT_TOPIC = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"

# Assinaturas de evento ERC1155 padrão (keccak256 do nome+tipos) -- não são
# específicas do Polymarket, são o standard EIP-1155. Calculadas com uma
# implementação Keccak-256 própria e validadas contra vetores de teste
# conhecidos (ver _keccak_test.py) -- a primeira versão desta constante
# TransferSingle estava truncada num caractere (erro de transcrição manual).
TRANSFER_SINGLE_TOPIC = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
TRANSFER_BATCH_TOPIC = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"

for _name, _topic in [("TRANSFER_SINGLE_TOPIC", TRANSFER_SINGLE_TOPIC),
                       ("TRANSFER_BATCH_TOPIC", TRANSFER_BATCH_TOPIC)]:
    assert len(_topic) == 66, f"{_name} tem comprimento errado: {_topic!r} ({len(_topic)} chars, devia ser 66)"

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

SESSION = requests.Session()
_RATE_LIMIT_SLEEP = 0.25  # ~4 pedidos/s, seguro para a key gratuita (5/s)


def _ps_get(params: dict, api_key: str) -> dict:
    """
    Chama a Etherscan API V2 (unificada) -- a antiga api.polygonscan.com
    foi descontinuada em ago/2025. `chainid=137` seleciona a rede Polygon.
    """
    params = {**params, "chainid": POLYGON_CHAIN_ID, "apikey": api_key}
    for attempt in range(4):
        resp = SESSION.get(POLYGONSCAN_BASE, params=params, timeout=20)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(
                f"Resposta não é JSON válido (status HTTP {resp.status_code}): "
                f"{resp.text[:200]!r}"
            )
        # Polygonscan devolve status "0" tanto para "sem resultados" como
        # para alguns erros -- distinguir pela mensagem
        result_msg = str(data.get("result") or "")
        if data.get("status") == "0" and "rate limit" in result_msg.lower():
            time.sleep(1.5 * (attempt + 1))
            continue
        if data.get("status") == "0" and any(
            kw in result_msg.lower() for kw in ("invalid api key", "deprecated", "notok")
        ):
            raise RuntimeError(f"Erro da API Etherscan/Polygonscan: {result_msg}")
        time.sleep(_RATE_LIMIT_SLEEP)
        return data
    raise RuntimeError("Rate limit do Polygonscan persistente após várias tentativas.")


def get_block_by_timestamp(ts: datetime, closest: str = "before", api_key: str = "") -> int | None:
    epoch = int(ts.timestamp())
    data = _ps_get({
        "module": "block", "action": "getblocknobytime",
        "timestamp": epoch, "closest": closest,
    }, api_key)
    if data.get("status") == "1" or data.get("message") == "OK":
        try:
            return int(data["result"])
        except (KeyError, ValueError, TypeError):
            return None
    return None


def _decode_transfer_single(data_hex: str) -> tuple[int, int] | None:
    """data = id (32 bytes) + value (32 bytes)."""
    h = data_hex[2:] if data_hex.startswith("0x") else data_hex
    if len(h) < 128:
        return None
    try:
        token_id = int(h[0:64], 16)
        value = int(h[64:128], 16)
        return token_id, value
    except ValueError:
        return None


def _decode_transfer_batch_simple(data_hex: str) -> list[tuple[int, int]]:
    """
    Decodificação simplificada de TransferBatch para o caso comum
    (arrays curtos, sem padding estranho). Devolve [] se não conseguir
    decodificar com confiança -- melhor perder um evento raro do que
    dar resultados errados.
    """
    h = data_hex[2:] if data_hex.startswith("0x") else data_hex
    try:
        # layout ABI: offset_ids(32) + offset_values(32) + len_ids(32) +
        # ids... + len_values(32) + values...
        offset_ids = int(h[0:64], 16) * 2  # em chars hex
        offset_values = int(h[64:128], 16) * 2
        len_ids = int(h[offset_ids:offset_ids + 64], 16)
        ids = []
        for i in range(len_ids):
            start = offset_ids + 64 + i * 64
            ids.append(int(h[start:start + 64], 16))
        len_values = int(h[offset_values:offset_values + 64], 16)
        values = []
        for i in range(len_values):
            start = offset_values + 64 + i * 64
            values.append(int(h[start:start + 64], 16))
        if len(ids) != len(values):
            return []
        return list(zip(ids, values))
    except (ValueError, IndexError):
        return []


def _topic_to_address(topic: str) -> str:
    """Um topic de endereço vem com padding a 32 bytes -- extrai os últimos 20."""
    h = topic[2:] if topic.startswith("0x") else topic
    return "0x" + h[-40:]


def get_transaction_logs(tx_hash: str, api_key: str) -> dict:
    """
    Vai buscar o recibo de UMA transação específica e devolve os seus logs
    em bruto (endereço do contrato + topics + data de cada evento emitido).
    Serve para diagnosticar, a partir de uma transação real e conhecida,
    que contrato(s) e evento(s) o Polymarket realmente usa para liquidar
    uma combo -- em vez de adivinhar a partir de documentação genérica.
    """
    data = _ps_get({
        "module": "proxy", "action": "eth_getTransactionReceipt",
        "txhash": tx_hash,
    }, api_key)
    result = data.get("result")
    if not result:
        return {"erro": f"Sem resultado para {tx_hash}", "resposta_bruta": data}
    logs = result.get("logs", [])
    return {
        "tx_hash": tx_hash,
        "status": result.get("status"),
        "n_logs": len(logs),
        "logs": [
            {
                "address": log.get("address"),
                "topics": log.get("topics"),
                "data": log.get("data"),
            }
            for log in logs
        ],
    }


def get_contract_source(address: str, api_key: str) -> dict:
    """
    Verifica se um contrato tem código-fonte verificado publicamente --
    se tiver, dá-nos a estrutura real dos eventos (ABI) em vez de termos
    de adivinhar a partir dos topics em bruto.
    """
    data = _ps_get({
        "module": "contract", "action": "getsourcecode",
        "address": address,
    }, api_key)
    result = data.get("result")
    if not result or not isinstance(result, list):
        return {"erro": "sem resposta", "resposta_bruta": data}
    info = result[0]
    return {
        "address": address,
        "contract_name": info.get("ContractName"),
        "is_verified": bool(info.get("SourceCode")),
        "abi_disponivel": info.get("ABI") not in (None, "", "Contract source code not verified"),
        "proxy": info.get("Proxy"),
        "implementation": info.get("Implementation"),
    }


def get_combo_settlement_wallets(
    from_block: int,
    to_block: int,
    api_key: str,
    max_pages: int = 20,
    on_progress=None,
) -> tuple[set[str], dict]:
    """
    Descoberta EXAUSTIVA de participantes em combos, via o evento real de
    liquidação do CombosExchange (confirmado contra uma transação real).

    Cada ocorrência do evento tem duas wallets como parâmetros indexados
    (topics[2] e topics[3]) -- a que pediu a combo (requester) e a que
    forneceu a liquidez para essa perna (market maker). Não sabemos ao
    certo qual é qual em cada caso, por isso adicionamos AMBAS ao conjunto
    de candidatas -- a verificação seguinte (via API oficial do Polymarket)
    é que confirma com certeza se cada uma tem mesmo uma combo relevante
    para o jogo em questão, por isso incluir candidatas a mais não estraga
    o resultado, só o torna mais lento.

    IMPORTANTE: isto varre TODAS as combos da plataforma nesse intervalo de
    blocos, não só as do jogo em causa -- é por isso mais lento, mas é a
    forma correta de não perder nenhuma.
    """
    on_progress = on_progress or (lambda msg: None)
    wallets: set[str] = set()
    debug = {"n_logs": 0, "n_paginas": 0}

    page = 1
    while page <= max_pages:
        data = _ps_get({
            "module": "logs", "action": "getLogs",
            "address": COMBOS_EXCHANGE,
            "topic0": COMBO_SETTLEMENT_TOPIC,
            "fromBlock": from_block, "toBlock": to_block,
            "page": page, "offset": 1000,
        }, api_key)
        debug["n_paginas"] += 1
        result = data.get("result")
        if page == 1:
            debug["pagina1_status"] = data.get("status")
            debug["pagina1_message"] = data.get("message")
        if not result or not isinstance(result, list):
            break
        on_progress(f"  Liquidações de combo página {page}: {len(result)} logs")
        for log in result:
            topics = log.get("topics", [])
            if len(topics) < 4:
                continue
            debug["n_logs"] += 1
            for topic in (topics[2], topics[3]):
                addr = _topic_to_address(topic)
                if addr.lower() != ZERO_ADDRESS and addr.lower() != COMBOS_EXCHANGE.lower():
                    wallets.add(addr)
        if len(result) < 1000:
            break
        page += 1

    on_progress(f"Liquidações de combo: {debug['n_logs']} eventos, {len(wallets)} wallets candidatas")
    return wallets, debug


def get_ctf_transfer_wallets(
    token_ids: set[str],
    from_block: int,
    to_block: int,
    api_key: str,
    max_pages: int = 20,
    on_progress=None,
) -> tuple[set[str], dict]:
    """
    Devolve o conjunto de wallets (from/to) envolvidas em transferências
    dos token_ids indicados, dentro do intervalo de blocos dado.
    """
    on_progress = on_progress or (lambda msg: None)
    target_ids = {int(t) for t in token_ids}
    wallets: set[str] = set()
    debug = {"n_logs_transfer_single": 0, "n_logs_transfer_batch": 0,
             "n_transfer_batch_ignorados": 0, "n_paginas": 0,
             "target_ids_amostra": [str(t) for t in list(target_ids)[:3]],
             "ids_vistos_amostra": []}
    seen_ids_sample: set[int] = set()

    for topic0, label in [(TRANSFER_SINGLE_TOPIC, "TransferSingle"), (TRANSFER_BATCH_TOPIC, "TransferBatch")]:
        page = 1
        n_paginas_este_topico = 0
        while page <= max_pages:
            data = _ps_get({
                "module": "logs", "action": "getLogs",
                "address": CTF_CONTRACT,
                "topic0": topic0,
                "fromBlock": from_block, "toBlock": to_block,
                "page": page, "offset": 1000,
            }, api_key)
            debug["n_paginas"] += 1
            n_paginas_este_topico += 1
            result = data.get("result")
            if page == 1:
                # guardar a resposta bruta da 1ª página se vier vazia/erro,
                # para diagnosticar sem adivinhar (ex: topic0 errado dava
                # status "0" com uma mensagem, não simplesmente [])
                debug[f"{label}_pagina1_status"] = data.get("status")
                debug[f"{label}_pagina1_message"] = data.get("message")
                if not result:
                    debug[f"{label}_pagina1_result_bruto"] = str(data.get("result"))[:150]
            if not result or not isinstance(result, list):
                break
            on_progress(f"  {label} página {page}: {len(result)} logs")
            for log in result:
                topics = log.get("topics", [])
                if len(topics) < 4:
                    continue
                from_addr = _topic_to_address(topics[2])
                to_addr = _topic_to_address(topics[3])
                log_data = log.get("data", "")

                if topic0 == TRANSFER_SINGLE_TOPIC:
                    debug["n_logs_transfer_single"] += 1
                    decoded = _decode_transfer_single(log_data)
                    if not decoded:
                        continue
                    token_id, _value = decoded
                    if len(seen_ids_sample) < 8:
                        seen_ids_sample.add(token_id)
                    if token_id in target_ids:
                        for addr in (from_addr, to_addr):
                            if addr.lower() != ZERO_ADDRESS:
                                wallets.add(addr)
                else:
                    debug["n_logs_transfer_batch"] += 1
                    pairs = _decode_transfer_batch_simple(log_data)
                    if not pairs:
                        debug["n_transfer_batch_ignorados"] += 1
                        continue
                    for tid, _v in pairs:
                        if len(seen_ids_sample) < 8:
                            seen_ids_sample.add(tid)
                    if any(tid in target_ids for tid, _v in pairs):
                        for addr in (from_addr, to_addr):
                            if addr.lower() != ZERO_ADDRESS:
                                wallets.add(addr)

            if len(result) < 1000:
                break
            page += 1
        debug[f"{label}_n_paginas"] = n_paginas_este_topico

    debug["ids_vistos_amostra"] = [str(t) for t in seen_ids_sample]
    return wallets, debug


def discover_wallets_onchain(
    event: dict,
    api_key: str,
    on_progress=None,
    window_hours_before: float = 2.0,
    window_hours_after: float = 6.0,
    skip_ctf_scan: bool = True,
) -> tuple[set[str], dict]:
    """
    Ponto de entrada principal: dado um evento (jogo) do Polymarket, junta
    os positionIds de todos os mercados, calcula um intervalo de blocos à
    volta da hora real do jogo (startTime), e lê os logs on-chain.

    skip_ctf_scan: por omissão True -- a vigilância de transferências
    diretas dos tokens das pernas (CTF/PositionManager) NUNCA encontra
    combos (confirmado com dados reais), só serve para holders normais que
    a descoberta habitual já apanha de outra forma. Saltar isto poupa
    aproximadamente metade do tempo do modo exaustivo.
    """
    on_progress = on_progress or (lambda msg: None)
    debug: dict = {}

    token_ids: set[str] = set()
    for market in event.get("markets", []):
        for pid in market.get("positionIds", []) or []:
            token_ids.add(str(pid))
    debug["n_token_ids"] = len(token_ids)
    if not token_ids and not skip_ctf_scan:
        debug["erro"] = "sem positionIds nos mercados deste evento"
        return set(), debug

    start_str = event.get("startTime") or event.get("startDate")
    try:
        match_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    except Exception:
        match_start = datetime.now(timezone.utc)

    now_utc = datetime.now(timezone.utc)
    from_ts = match_start.timestamp() - window_hours_before * 3600
    to_ts = match_start.timestamp() + window_hours_after * 3600
    from_dt = datetime.fromtimestamp(from_ts, tz=timezone.utc)
    to_dt = datetime.fromtimestamp(to_ts, tz=timezone.utc)

    # nunca pedir um bloco no futuro -- a Etherscan/Polygonscan não
    # consegue converter um timestamp que ainda não aconteceu (a
    # blockchain ainda não produziu esses blocos), o que causava o erro
    # "não consegui converter timestamps em blocos" quando o jogo ainda
    # estava a decorrer ou tinha acabado há pouco tempo
    to_block_mode = "after"
    if to_dt > now_utc:
        on_progress(f"Janela ia até ao futuro ({to_dt.isoformat()}) -- a limitar a 'agora'.")
        to_dt = now_utc
        to_block_mode = "before"
    if from_dt > now_utc:
        from_dt = now_utc - timedelta(minutes=5)
        to_block_mode = "before"

    on_progress(f"A converter janela temporal ({from_dt.isoformat()} .. {to_dt.isoformat()}) em blocos...")
    from_block = get_block_by_timestamp(from_dt, "before", api_key)
    to_block = get_block_by_timestamp(to_dt, to_block_mode, api_key)
    debug["from_block"] = from_block
    debug["to_block"] = to_block
    if from_block is None or to_block is None:
        debug["erro"] = "não consegui converter timestamps em blocos (ver chave da API)"
        return set(), debug

    on_progress(f"A ler logs on-chain entre os blocos {from_block} e {to_block}...")

    # Fonte 1 (a que realmente importa para combos): eventos de liquidação
    # do CombosExchange -- varre TODAS as combos da plataforma nesse
    # intervalo, não só as deste jogo, mas é a única forma de não perder
    # nenhuma. A verificação seguinte (API oficial) filtra o que é
    # relevante para este jogo especificamente.
    combo_wallets, combo_debug = get_combo_settlement_wallets(
        from_block, to_block, api_key, on_progress=on_progress,
    )
    debug["combo_settlement"] = combo_debug

    # Fonte 2 (complementar, opcional): transferências diretas dos tokens
    # das pernas -- não apanha combos (ver descoberta documentada acima),
    # só holders "normais" que a descoberta habitual possa ter perdido.
    # Por omissão SALTADA (skip_ctf_scan=True) para poupar tempo, já que
    # não ajuda o objetivo principal (combos).
    ctf_wallets: set[str] = set()
    if skip_ctf_scan:
        debug["ctf_transfers"] = {"saltado": True}
    elif token_ids:
        ctf_wallets, ctf_debug = get_ctf_transfer_wallets(
            token_ids, from_block, to_block, api_key, on_progress=on_progress,
        )
        debug["ctf_transfers"] = ctf_debug

    wallets = combo_wallets | ctf_wallets
    on_progress(f"Encontradas {len(wallets)} wallets via leitura on-chain "
                f"({len(combo_wallets)} via combos, {len(ctf_wallets)} via transferências CTF).")
    return wallets, debug

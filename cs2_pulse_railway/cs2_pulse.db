"""
CS2 Pulse -- tracker de jogos CS2 + inteligência de mercado Polymarket.

Correr com:
    streamlit run app.py
"""

from __future__ import annotations

import hmac
import os
import time

import pandas as pd
import streamlit as st

import liquipedia_client as lq
import onchain_discovery as onchain
import polymarket_engine as pm
import storage
import fun88_odds as fun88

st.set_page_config(page_title="CS2 Pulse", page_icon="🎯", layout="wide")
storage.init_db()
storage.maybe_cleanup()

# ---------------------------------------------------------------------------
# Acesso privado -- sem isto, qualquer pessoa com o URL acede à app (e aos
# tokens/chaves embutidas no código: Fun88, Polygonscan). Define a variável
# de ambiente CS2_PULSE_PASSWORD antes de pores isto acessível pela internet
# -- localmente já vem definida no run_app.bat.
# ---------------------------------------------------------------------------

APP_PASSWORD = os.environ.get("CS2_PULSE_PASSWORD", "")


def _check_password() -> bool:
    if st.session_state.get("cs2_pulse_authed"):
        return True

    st.title("🎯 CS2 Pulse")
    st.caption("Acesso privado")

    if not APP_PASSWORD:
        st.warning(
            "CS2_PULSE_PASSWORD não está definida -- a app está sem "
            "proteção nenhuma. Define essa variável de ambiente antes de "
            "expores isto pela internet."
        )
        st.session_state["cs2_pulse_authed"] = True
        st.rerun()

    pwd = st.text_input("Password", type="password", key="cs2_pulse_pwd_input")
    if st.button("Entrar"):
        if hmac.compare_digest(pwd, APP_PASSWORD):
            st.session_state["cs2_pulse_authed"] = True
            st.rerun()
        else:
            st.error("Password incorreta.")
    return False


if not _check_password():
    st.stop()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def df_from_rows(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["categoria"] = df["wallet_e_holder_do_jogo"].map({True: "HOLDER", False: "SÓ-COMBO"})
    return df


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Combos CS2")
    return buf.getvalue()


def render_combo_results(rows: list[dict], event_title: str):
    df = df_from_rows(rows)
    if df.empty:
        st.info("Nenhuma combo oficial (isCombo=true) encontrada para este jogo.")
        return

    n_combos = df["combo_condition_id"].nunique()
    n_wallets = df["wallet"].nunique()
    total_vol = df["valor_investido_usdc"].sum()
    n_holders = df[df["wallet_e_holder_do_jogo"]]["wallet"].nunique()
    n_so_combo = df[~df["wallet_e_holder_do_jogo"]]["wallet"].nunique()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Combos", n_combos)
    c2.metric("Wallets envolvidas", n_wallets)
    c3.metric("Volume investido", f"${total_vol:,.2f}")
    c4.metric("Holder vs Só-combo", f"{n_holders} / {n_so_combo}")

    # colunas mais importantes primeiro (o que escolheu + o resto da combo),
    # sem teres de fazer scroll horizontal para as ver
    col_order = [
        "categoria", "wallet", "opcao_escolhida", "outras_pernas_da_combo",
        "valor_investido_usdc", "n_pernas_totais", "jogo", "perna_titulo",
        "combo_condition_id", "tx_hash", "timestamp", "n_pernas_neste_jogo",
        "perna_condition_id", "wallet_e_holder_do_jogo",
    ]
    df = df[[c for c in col_order if c in df.columns]]

    tab_all, tab_holder, tab_socombo = st.tabs(["Todas", "Holders", "Só-combo"])
    with tab_all:
        st.dataframe(df, use_container_width=True, hide_index=True)
    with tab_holder:
        st.dataframe(df[df["wallet_e_holder_do_jogo"]], use_container_width=True, hide_index=True)
    with tab_socombo:
        st.dataframe(df[~df["wallet_e_holder_do_jogo"]], use_container_width=True, hide_index=True)

    st.download_button(
        "⬇️ Descarregar Excel",
        data=to_excel_bytes(df),
        file_name=f"combos_{event_title[:40].replace(' ', '_')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def run_analysis(event: dict, workers: int, polygonscan_key: str = "",
                  window_hours_before: float = 2.0, window_hours_after: float = 6.0,
                  restrict_activity_window: bool = False, activity_window_days: int = 7,
                  skip_ctf_scan: bool = True, use_wallet_cache: bool = True,
                  cache_ttl_minutes: int = 15) -> list[dict]:
    log_area = st.expander("Log detalhado", expanded=False)
    log_lines: list[str] = []
    progress_bar = st.progress(0.0, text="A iniciar...")

    def on_progress(msg: str):
        log_lines.append(msg)

    def on_stage(label: str, frac: float):
        progress_bar.progress(min(frac, 1.0), text=label)

    # wallets da watchlist são SEMPRE incluídas, além das descobertas
    # automaticamente -- a API do Polymarket não permite listar todos os
    # participantes de um mercado, por isso isto é a forma de garantires
    # que uma wallet que já sabes ser relevante nunca fica de fora
    watchlisted = [w["wallet"] for w in storage.list_watchlist_wallets()]

    # modo exaustivo (opcional): lê diretamente a blockchain para apanhar
    # TODAS as wallets que tocaram nos tokens de posição deste jogo, não só
    # as que aparecem em /trades ou no top de /holders. Mais lento, mas
    # muito mais completo.
    onchain_debug = None
    if polygonscan_key:
        with st.spinner("Modo exaustivo: a ler a blockchain (pode demorar mais)..."):
            try:
                onchain_wallets, onchain_debug = onchain.discover_wallets_onchain(
                    event, polygonscan_key, on_progress=on_progress,
                    window_hours_before=window_hours_before,
                    window_hours_after=window_hours_after,
                    skip_ctf_scan=skip_ctf_scan,
                )
                watchlisted = list(set(watchlisted) | onchain_wallets)
                on_progress(f"Modo exaustivo: +{len(onchain_wallets)} wallets via blockchain")
            except Exception as e:
                st.warning(f"Modo exaustivo falhou (a continuar sem ele): {e}")

    activity_start_ts = None
    if restrict_activity_window:
        try:
            start_str = event.get("startTime") or event.get("startDate")
            match_start = pd.Timestamp(start_str).to_pydatetime()
            activity_start_ts = int((match_start - pd.Timedelta(days=activity_window_days)).timestamp())
            on_progress(f"Verificação restrita a partir de {activity_start_ts} "
                        "(7 dias antes do jogo) -- mais rápido, pode perder combos mais antigas.")
        except Exception:
            pass

    # cache de verificações: wallets já verificadas recentemente para este
    # jogo, sem combo encontrada, podem ser saltadas -- acelera re-análises
    # do mesmo jogo (útil quando estás a ajustar definições e a repetir)
    event_slug = event.get("slug", "")
    skip_wallets: set[str] = set()
    if use_wallet_cache and event_slug:
        skip_wallets = storage.get_recently_checked_negative_wallets(
            event_slug, ttl_seconds=cache_ttl_minutes * 60,
        )
        if skip_wallets:
            on_progress(f"Cache: {len(skip_wallets)} wallets já verificadas "
                        f"nos últimos {cache_ttl_minutes} min (sem combo) -- vão ser saltadas")

    wallet_check_results: dict[str, bool] = {}

    def on_wallet_checked(wallet: str, found: bool):
        wallet_check_results[wallet] = found

    rows = pm.analyze_event(event, workers=workers, on_progress=on_progress, on_stage=on_stage,
                             extra_wallets=watchlisted, activity_start_ts=activity_start_ts,
                             skip_wallets=skip_wallets, on_wallet_checked=on_wallet_checked)

    if use_wallet_cache and event_slug and wallet_check_results:
        storage.record_wallet_checks(event_slug, wallet_check_results)

    log_area.code("\n".join(log_lines[-500:]))
    if onchain_debug:
        with st.expander("🔗 Diagnóstico do modo exaustivo (on-chain)"):
            st.json(onchain_debug)
    progress_bar.empty()

    row_dicts = pm.rows_to_dicts(rows)
    storage.save_analysis_run(
        event.get("title", event.get("slug", "?")),
        event.get("slug", ""),
        row_dicts,
    )
    return row_dicts


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("🎯 CS2 Pulse")
st.sidebar.caption("Tracker de jogos CS2 + inteligência de mercado (Polymarket)")
if st.sidebar.button("🚪 Sair"):
    st.session_state["cs2_pulse_authed"] = False
    st.rerun()

_pages = ["Calendário", "Analisar jogo", "Verificar wallet", "Smart money", "Odds Fun88", "Histórico", "Watchlist"]
if "nav_page" not in st.session_state:
    st.session_state["nav_page"] = "Calendário"
if st.session_state.pop("_nav_to_analyze", False):
    st.session_state["nav_page"] = "Analisar jogo"

page = st.sidebar.radio("Navegação", _pages, key="nav_page")
workers = st.sidebar.slider("Paralelismo (wallets em simultâneo)", 4, 50, 15)
st.sidebar.divider()
st.sidebar.subheader("Modo exaustivo (opcional)")
polygonscan_key = st.sidebar.text_input(
    "Chave API Polygonscan",
    value="H12HMYJJ2MMRE476XSGAJ1CQVN56GGCVFZ",
    type="password",
    help="Grátis em polygonscan.com/apis. Ativa leitura direta da blockchain "
         "para apanhar TODAS as wallets, mesmo as que a API do Polymarket "
         "não mostra (mais lento).",
)
col_w1, col_w2 = st.sidebar.columns(2)
window_hours_before = col_w1.number_input("Janela: horas antes", min_value=0.0, max_value=12.0, value=2.0, step=0.5)
window_hours_after = col_w2.number_input("Janela: horas depois", min_value=0.5, max_value=24.0, value=6.0, step=0.5)
st.sidebar.caption(
    "Janela mais estreita = modo exaustivo mais rápido, mas arrisca perder "
    "combos feitas fora dela (ex: muito antes do jogo começar)."
)
restrict_activity_window = st.sidebar.checkbox(
    "Acelerar verificação por wallet (restringir período)",
    value=False,
    help="Quando há muitas wallets candidatas (ex: modo exaustivo com "
         "centenas), verificar o histórico completo de cada uma é lento. "
         "Isto restringe a verificação de cada wallet a partir de alguns "
         "dias antes do jogo -- mais rápido, mas pode perder combos feitas "
         "muito antes dessa data.",
)
activity_window_days = 7
if restrict_activity_window:
    activity_window_days = st.sidebar.number_input(
        "Verificar a partir de quantos dias antes do jogo?",
        min_value=1, max_value=30, value=3, step=1,
    )
skip_ctf_scan = st.sidebar.checkbox(
    "Modo exaustivo: só combos (mais rápido)",
    value=True,
    help="A parte do modo exaustivo que vigia transferências diretas dos "
         "tokens das pernas NUNCA encontra combos (confirmado) -- só serve "
         "para holders normais, que já são apanhados pela descoberta "
         "habitual (/trades e /holders). Desligar isto poupa "
         "aproximadamente metade do tempo do modo exaustivo.",
)
use_wallet_cache = st.sidebar.checkbox(
    "Poupar re-análises do mesmo jogo (cache)",
    value=True,
    help="Wallets já verificadas recentemente para o mesmo jogo, sem combo "
         "encontrada, são saltadas em vez de reverificadas -- acelera "
         "bastante quando estás a repetir a análise do mesmo jogo (ex: ao "
         "ajustar definições). Wallets com combo são sempre reverificadas.",
)
cache_ttl_minutes = 15
if use_wallet_cache:
    cache_ttl_minutes = st.sidebar.number_input(
        "Cache válida durante quantos minutos?", min_value=1, max_value=120, value=15, step=5,
    )
st.sidebar.caption(
    "Sem chave: só descoberta normal (trades + top holders). "
    "Com chave: adiciona leitura on-chain, mais lenta mas mais completa."
)
st.sidebar.divider()
st.sidebar.caption(lq.ATTRIBUTION_NOTICE)

# ---------------------------------------------------------------------------
# Página: Calendário
# ---------------------------------------------------------------------------

if page == "Calendário":
    st.header("Calendário CS2")

    st.subheader("Jogos CS2 ativos no Polymarket")
    st.caption("Esta fonte já funciona -- dá-te o jogo pronto a analisar (clica para ires direto à análise de combos).")
    show_all = st.checkbox("Mostrar também mercados de futures/props (MVP, roster changes, etc.)", value=False)
    if st.button("Carregar jogos do Polymarket", type="primary"):
        with st.spinner("A consultar Polymarket..."):
            if show_all:
                events = pm.find_cs2_events(limit=60)
                debug_info = None
            else:
                events, debug_info = pm.find_cs2_matches(limit=40, scan=200, debug=True)
        # guardar em memória de sessão -- se não, os botões "Analisar" abaixo
        # desaparecem no próximo rerun (o botão "Carregar" só fica "clicado"
        # no preciso instante em que é premido, e o bloco todo desapareceria)
        st.session_state["calendar_events"] = events
        st.session_state["calendar_debug_info"] = debug_info

    events = st.session_state.get("calendar_events")
    debug_info = st.session_state.get("calendar_debug_info")

    if events is not None:
        if events:
            table_rows = []
            for e in events:
                start = e.get("startTime") or e.get("startDate", "")
                date_display = start[:16].replace("T", " ") if start else "?"
                table_rows.append({
                    "Ao vivo": "🔴" if e.get("live") else "",
                    "Jogo": e.get("title"),
                    "Início": date_display,
                    "Volume ($)": e.get("volume", 0) or 0,
                    "slug": e.get("slug"),
                })
            df_events = pd.DataFrame(table_rows)

            st.caption("Clica num cabeçalho de coluna para ordenar. Clica numa linha para a selecionar, depois usa o botão \"Analisar\" abaixo.")
            selection = st.dataframe(
                df_events.drop(columns=["slug"]),
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
            )

            selected_rows = selection.selection.rows if selection and selection.selection else []
            if selected_rows:
                selected_slug = df_events.iloc[selected_rows[0]]["slug"]
                selected_title = df_events.iloc[selected_rows[0]]["Jogo"]
                if st.button(f"Analisar '{selected_title[:50]}'", type="primary"):
                    st.session_state["prefill_slug"] = selected_slug
                    st.session_state["_nav_to_analyze"] = True
                    st.rerun()
        else:
            st.info(
                "Nenhum jogo (confronto direto) CS2 encontrado neste momento -- "
                "pode não haver jogos agendados nas próximas horas. Tenta marcar "
                "a opção acima para veres todos os mercados CS2, incluindo futures."
            )

        if debug_info:
            with st.expander("🔍 Diagnóstico (para eu perceber o que está a acontecer)",
                              expanded=not events):
                for i, attempt in enumerate(debug_info["attempts"], start=1):
                    st.write(f"**Tentativa {i}:** {attempt.get('method', '?')}")
                    if "error" in attempt:
                        st.write(f"Erro: {attempt['error']}")
                    if "subtag_discovery" in attempt:
                        st.write("Descoberta de sub-tags:")
                        st.json(attempt["subtag_discovery"])
                    if "resultados_por_tag" in attempt:
                        st.write("Eventos encontrados por sub-tag:")
                        st.json(attempt["resultados_por_tag"])
                    if "n_events" in attempt:
                        st.write(f"{attempt['n_events']} eventos devolvidos")
                        for t in attempt.get("sample_titles", []):
                            st.text(f"  • {t}")
                st.caption("Copia/tira print a isto e envia -- diz-me exatamente "
                           "o que a API está a devolver, em vez de continuarmos a adivinhar.")

    st.divider()
    with st.expander("🔬 Diagnóstico de evento (avançado) -- ver estrutura real de um jogo"):
        st.caption(
            "Cola o URL ou slug de um jogo que sabes que existe (ex: um confronto "
            "direto que vês no site mas que a app não apanha) para vermos os campos "
            "reais que o Polymarket devolve -- tags, series, etc."
        )
        diag_input = st.text_input("URL ou slug do jogo", key="diag_event_input")
        if st.button("Inspecionar evento", key="diag_event_btn") and diag_input:
            try:
                diag_slug = pm.extract_slug_from_url(diag_input)
                diag_event = pm.get_event_by_slug(diag_slug)
                if not diag_event:
                    st.error(f"/events?slug={diag_slug} não devolveu nada.")
                else:
                    st.write("**Campos de topo do evento:**", list(diag_event.keys()))
                    if "tags" in diag_event:
                        st.write("**tags:**")
                        st.json(diag_event["tags"])
                    for field in ["series", "seriesSlug", "gameId", "category", "tagIds", "tag"]:
                        if field in diag_event:
                            st.write(f"**{field}:**", diag_event[field])
                    st.write("**Evento completo (bruto):**")
                    st.json(diag_event)
            except Exception as e:
                st.error(f"Erro: {e}")

    with st.expander("🔬 Diagnóstico de transação (avançado) -- ver logs reais on-chain"):
        st.caption(
            "Cola o hash de uma transação Polygon conhecida (ex: uma combo "
            "confirmada) para vermos os eventos reais que ela emitiu -- "
            "endereços de contrato e topics. Isto diz-nos com certeza que "
            "contrato/evento o Polymarket usa para liquidar combos, em vez "
            "de adivinhar a partir de documentação genérica."
        )
        tx_input = st.text_input("Hash da transação (0x...)", key="diag_tx_input")
        if st.button("Inspecionar transação", key="diag_tx_btn") and tx_input:
            if not polygonscan_key:
                st.error("Precisas de preencher a chave API Polygonscan na barra lateral primeiro.")
            else:
                with st.spinner("A consultar a transação..."):
                    try:
                        result = onchain.get_transaction_logs(tx_input.strip(), polygonscan_key)
                        st.json(result)
                        if result.get("logs"):
                            st.caption(
                                "Para cada log: `address` é o contrato que emitiu o evento, "
                                "`topics[0]` é a assinatura do evento (o que procuramos "
                                "identificar), `topics[1:]` são os parâmetros indexados."
                            )
                    except Exception as e:
                        st.error(f"Erro: {e}")

        st.divider()
        st.caption(
            "Descoberta importante: as combos usam o contrato `PositionManager` "
            "(0x006F54F7f9A22e0000CC2AB60031000000ae9fEF), com tokens sintéticos "
            "próprios -- não movem diretamente os tokens das pernas individuais. "
            "Verifica abaixo se há código-fonte público para decodificar isto corretamente."
        )
        if st.button("Verificar se o PositionManager tem código-fonte público", key="diag_source_btn"):
            if not polygonscan_key:
                st.error("Precisas da chave API Polygonscan na barra lateral.")
            else:
                with st.spinner("A consultar..."):
                    try:
                        info = onchain.get_contract_source(
                            "0x006F54F7f9A22e0000CC2AB60031000000ae9fEF", polygonscan_key,
                        )
                        st.json(info)
                    except Exception as e:
                        st.error(f"Erro: {e}")

    st.divider()
    st.subheader("Calendário Liquipedia (experimental)")
    st.warning(
        "⚠️ Esta integração não está confirmada como funcional. A Liquipedia "
        "mudou para um sistema de API (LiquipediaDB v3) que exige uma chave "
        "pedida manualmente através do formulário deles -- sem essa chave, esta "
        "secção usa o método antigo sem key, cujos nomes de propriedades ainda "
        "não foram validados contra a wiki ao vivo. Usa a secção do Polymarket "
        "acima como calendário principal por agora."
    )
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Próximos jogos**")
        if st.button("Tentar carregar próximos jogos"):
            with st.spinner("A consultar Liquipedia (respeita 1 pedido/2s)..."):
                try:
                    matches = lq.get_upcoming_matches(limit=20)
                    if matches:
                        st.dataframe(pd.DataFrame(matches), use_container_width=True, hide_index=True)
                    else:
                        st.warning("Lista vazia -- os nomes das propriedades precisam de ajuste (ver liquipedia_client.py).")
                except Exception as e:
                    st.error(f"Falha ao consultar Liquipedia: {e}")
    with col2:
        st.markdown("**Resultados recentes**")
        if st.button("Tentar carregar resultados recentes"):
            with st.spinner("A consultar Liquipedia..."):
                try:
                    results = lq.get_recent_results(limit=20)
                    if results:
                        st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
                    else:
                        st.warning("Lista vazia -- ver nota acima.")
                except Exception as e:
                    st.error(f"Falha ao consultar Liquipedia: {e}")

# ---------------------------------------------------------------------------
# Página: Analisar jogo
# ---------------------------------------------------------------------------

elif page == "Analisar jogo":
    st.header("Analisar combos de um jogo")
    prefill = st.session_state.pop("prefill_slug", "")
    url_or_slug = st.text_input(
        "URL ou slug do jogo no Polymarket",
        value=prefill,
        placeholder="https://polymarket.com/event/nome-do-jogo",
    )
    if st.button("Analisar", type="primary", disabled=not url_or_slug):
        try:
            slug = pm.extract_slug_from_url(url_or_slug)
        except ValueError as e:
            st.error(str(e))
            st.stop()

        with st.spinner(f"A procurar jogo '{slug}'..."):
            event = pm.resolve_event(slug)
        if not event:
            st.error(f"Não encontrei nenhum evento ou mercado com o slug '{slug}'.")
            st.stop()

        title = event.get("title", slug)
        n_markets = len(pm.markets_for_event(event))
        st.success(f"Encontrado: **{title}** ({n_markets} mercado(s))")

        rows = run_analysis(event, workers, polygonscan_key, window_hours_before, window_hours_after,
                             restrict_activity_window, activity_window_days, skip_ctf_scan,
                             use_wallet_cache, cache_ttl_minutes)
        # guardar em memória de sessão -- se não, clicar no botão da
        # watchlist abaixo faria "Analisar" voltar a False no próximo rerun
        # e perderias os resultados já calculados, obrigando a repetir tudo
        st.session_state["last_analyzed_event"] = event
        st.session_state["last_analyzed_rows"] = rows

    last_event = st.session_state.get("last_analyzed_event")
    last_rows = st.session_state.get("last_analyzed_rows")
    if last_event:
        title = last_event.get("title", last_event.get("slug", "?"))
        if st.button(f"➕ Adicionar '{title[:40]}' à watchlist"):
            storage.add_watchlist_game(last_event.get("slug", ""), title)
            st.toast("Adicionado à watchlist")
        render_combo_results(last_rows, title)

# ---------------------------------------------------------------------------
# Página: Verificar wallet
# ---------------------------------------------------------------------------

elif page == "Verificar wallet":
    st.header("Verificar uma wallet específica num jogo")
    col1, col2 = st.columns(2)
    with col1:
        wallet_input = st.text_input("Wallet (0x..., URL de perfil, ou username/@username)")
    with col2:
        game_input = st.text_input("URL ou slug do jogo")

    if st.button("Verificar", type="primary", disabled=not (wallet_input and game_input)):
        with st.spinner("A resolver wallet e jogo..."):
            wallet = pm.resolve_wallet_input(wallet_input)
            if not wallet:
                st.error(f"Não consegui resolver '{wallet_input}' para um endereço de wallet.")
                st.stop()
            if wallet.lower() != wallet_input.strip().lower():
                st.caption(f"'{wallet_input}' resolvido para: `{wallet}`")

            try:
                slug = pm.extract_slug_from_url(game_input)
            except ValueError as e:
                st.error(str(e))
                st.stop()
            event = pm.resolve_event(slug)
            if not event:
                st.error(f"Não encontrei nenhum evento com o slug '{slug}'.")
                st.stop()

        title = event.get("title", slug)
        condition_ids = [m["conditionId"] for m in pm.markets_for_event(event) if m.get("conditionId")]

        with st.spinner("A consultar atividade..."):
            filtered_rows = pm.get_activity(wallet, market_ids=condition_ids, type_="TRADE")
            all_rows = pm.get_activity(wallet, market_ids=None, type_="TRADE")
            combo_only = [r for r in all_rows if r.get("isCombo")]
            combo_rows = pm.find_combos_for_wallet(wallet, title, condition_ids)

        # guardar em memória de sessão -- se não, o botão da watchlist abaixo
        # faria "Verificar" voltar a False no próximo rerun e perderias tudo
        st.session_state["wallet_check_result"] = {
            "wallet": wallet,
            "wallet_input": wallet_input,
            "title": title,
            "condition_ids": condition_ids,
            "filtered_rows": filtered_rows,
            "all_rows": all_rows,
            "combo_only": combo_only,
            "combo_rows_dicts": pm.rows_to_dicts(combo_rows),
        }

    result = st.session_state.get("wallet_check_result")
    if result:
        st.write(f"**Jogo:** {result['title']} ({len(result['condition_ids'])} mercado(s))")
        st.write(f"**Wallet:** `{result['wallet']}`")

        st.subheader("Trades normais neste jogo")
        if result["filtered_rows"]:
            st.dataframe(pd.DataFrame(result["filtered_rows"]), use_container_width=True, hide_index=True)
        else:
            st.caption("Nenhum trade normal encontrado nestes mercados.")

        st.subheader(f"Atividade com isCombo=true ({len(result['combo_only'])} de {len(result['all_rows'])} linhas totais)")
        if result["combo_only"]:
            st.dataframe(pd.DataFrame(result["combo_only"]), use_container_width=True, hide_index=True)

        st.subheader("Resultado")
        if not result["combo_rows_dicts"]:
            st.info("Nenhuma combo oficial encontrada para esta wallet neste jogo.")
        else:
            render_combo_results(result["combo_rows_dicts"], result["title"])

        if st.button("➕ Adicionar wallet à watchlist"):
            label = result["wallet_input"] if not result["wallet_input"].startswith("0x") else ""
            storage.add_watchlist_wallet(result["wallet"], label)
            st.toast("Wallet adicionada à watchlist")

# ---------------------------------------------------------------------------
# Página: Smart money
# ---------------------------------------------------------------------------

elif page == "Smart money":
    st.header("Smart money -- leaderboard Polymarket")
    st.caption(
        "A API do Polymarket só tem uma categoria genérica 'Esports' "
        "(inclui LoL, Dota, Valorant, etc.) -- não existe uma categoria "
        "específica para CS2. Para chegar só a CS2, cruza o leaderboard "
        "com atividade real nos jogos CS2 atuais (abaixo)."
    )
    col_cat, col_period = st.columns(2)
    category = col_cat.selectbox(
        "Categoria", ["ESPORTS", "OVERALL", "SPORTS", "POLITICS", "CRYPTO"], index=0,
    )
    time_period = col_period.selectbox("Período", ["DAY", "WEEK", "MONTH", "ALL"], index=0)
    only_cs2 = st.checkbox(
        "Filtrar só traders com atividade CS2 (mais lento -- cruza com jogos CS2 ativos)",
        value=(category == "ESPORTS"),
    )

    if st.button("Carregar leaderboard"):
        with st.spinner("A consultar leaderboard..."):
            try:
                lb = pm.get_leaderboard(category=category, time_period=time_period, limit=100)
            except Exception as e:
                st.error(f"Falha ao consultar leaderboard: {e}")
                lb = []

        if lb and only_cs2:
            with st.spinner("A cruzar com jogos CS2 ativos (uma chamada por trader)..."):
                try:
                    cs2_events = pm.find_cs2_matches(limit=15, scan=200)
                    cs2_condition_ids = [
                        mkt["conditionId"]
                        for ev in cs2_events
                        for mkt in pm.markets_for_event(ev)
                        if mkt.get("conditionId")
                    ]
                    if cs2_condition_ids:
                        lb = pm.filter_leaderboard_by_cs2_activity(lb, cs2_condition_ids)
                    else:
                        st.warning("Não encontrei jogos CS2 ativos para cruzar -- a mostrar leaderboard sem filtro.")
                except Exception as e:
                    st.warning(f"Falha ao cruzar com CS2 (a mostrar leaderboard sem filtro): {e}")

        if lb:
            df = pd.DataFrame(lb)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.session_state["leaderboard"] = lb
        else:
            st.info("Sem dados de leaderboard neste momento" +
                    (" (ou nenhum trader deste leaderboard tem atividade CS2 recente)." if only_cs2 else "."))

    if "leaderboard" in st.session_state:
        st.divider()
        st.subheader("Ver se um trader do leaderboard está num jogo específico")
        options = {
            f"{e.get('userName', e.get('proxyWallet', '?'))} (vol ${e.get('vol', 0):,.0f})": e.get(
                "wallet", e.get("proxyWallet"))
            for e in st.session_state["leaderboard"]
        }
        chosen = st.selectbox("Trader", list(options.keys()))
        game_input2 = st.text_input("URL ou slug do jogo", key="smart_money_game")
        if st.button("Verificar este trader", disabled=not game_input2):
            wallet = options[chosen]
            try:
                slug = pm.extract_slug_from_url(game_input2)
            except ValueError as e:
                st.error(str(e))
                st.stop()
            event = pm.resolve_event(slug)
            if not event:
                st.error("Jogo não encontrado.")
                st.stop()
            condition_ids = [m["conditionId"] for m in pm.markets_for_event(event) if m.get("conditionId")]
            with st.spinner("A verificar..."):
                combo_rows = pm.find_combos_for_wallet(wallet, event.get("title", slug), condition_ids)
                filtered = pm.get_activity(wallet, market_ids=condition_ids, type_="TRADE")
            if filtered:
                st.write("Trades normais neste jogo:")
                st.dataframe(pd.DataFrame(filtered), use_container_width=True, hide_index=True)
            if combo_rows:
                render_combo_results(pm.rows_to_dicts(combo_rows), event.get("title", slug))
            if not filtered and not combo_rows:
                st.info("Este trader não tem atividade detetada neste jogo.")

# ---------------------------------------------------------------------------
# Página: Odds Fun88
# ---------------------------------------------------------------------------

elif page == "Odds Fun88":
    st.header("Fun88 -- movimento de odds (CS2)")
    st.caption(
        "Jogos de CS2 de hoje na Fun88, ordenados pela maior variação de "
        "probabilidade implícita entre a odd de abertura e a odd atual. "
        "Fonte: TF Gaming via Fun88, engenharia reversa de uma API não "
        "documentada -- pode parar de funcionar sem aviso. Se isso "
        "acontecer, vê as instruções no topo de fun88_odds.py."
    )

    refresh = st.button("🔄 Atualizar agora")
    if refresh:
        st.cache_data.clear()

    @st.cache_data(ttl=300, show_spinner="A consultar a Fun88...")
    def _load_fun88_odds():
        return fun88.fetch_odds_movement()

    try:
        rows = _load_fun88_odds()
    except Exception as e:
        st.error(f"Falha ao consultar a Fun88: {e}")
        rows = []

    if not rows:
        st.info("Sem dados (ou nenhum jogo de CS2 hoje na Fun88).")
    else:
        df = pd.DataFrame(rows)
        df["casa"] = (
            df["home_team"] + "  ("
            + df["opening_home_euro"].astype(str) + " → "
            + df["current_home_euro"].astype(str) + ")"
        )
        df["fora"] = (
            df["away_team"] + "  ("
            + df["opening_away_euro"].astype(str) + " → "
            + df["current_away_euro"].astype(str) + ")"
        )
        df["variação (pp)"] = df["prob_swing_pp"]

        big_swing = df[df["variação (pp)"].abs() >= 10]
        c1, c2 = st.columns(2)
        c1.metric("Jogos analisados", len(df))
        c2.metric("Com variação ≥10pp", len(big_swing))

        show_cols = [
            "casa", "fora", "variação (pp)", "competition_name",
            "start_datetime", "best_of", "in_play",
        ]
        st.dataframe(
            df[show_cols].sort_values("variação (pp)", key=lambda s: s.abs(), ascending=False),
            use_container_width=True, hide_index=True,
        )

        st.divider()
        st.subheader("Detalhe: evolução das odds de um jogo")

        df_sorted = df.sort_values("variação (pp)", key=lambda s: s.abs(), ascending=False)
        options = {
            f"{r.home_team} vs {r.away_team} — {r.competition_name} "
            f"({r.prob_swing_pp:+.1f}pp)": r.market_id
            for r in df_sorted.itertuples()
        }
        chosen_label = st.selectbox("Escolhe um jogo", list(options.keys()))

        if st.button("📈 Ver evolução"):
            market_id = options[chosen_label]
            chosen_row = df_sorted[df_sorted["market_id"] == market_id].iloc[0]

            with st.spinner("A carregar histórico de odds..."):
                try:
                    series = fun88.fun88_odds_series(market_id)
                except Exception as e:
                    st.error(f"Falha ao carregar o histórico: {e}")
                    series = []

            if not series:
                st.info("Sem histórico de odds disponível para este jogo.")
            else:
                sdf = pd.DataFrame(series)
                sdf["datetime"] = pd.to_datetime(sdf["datetime"])

                st.caption(
                    f"**{chosen_row.home_team}** (casa) vs **{chosen_row.away_team}** (fora) "
                    f"— {chosen_row.competition_name}"
                )

                prob_chart = sdf.set_index("datetime")[["home_prob", "away_prob"]].rename(
                    columns={
                        "home_prob": f"{chosen_row.home_team} (prob.)",
                        "away_prob": f"{chosen_row.away_team} (prob.)",
                    }
                )
                st.line_chart(prob_chart)

                euro_chart = sdf.set_index("datetime")[["home_euro", "away_euro"]].rename(
                    columns={
                        "home_euro": f"{chosen_row.home_team} (odd)",
                        "away_euro": f"{chosen_row.away_team} (odd)",
                    }
                )
                st.line_chart(euro_chart)

                with st.expander("Ver tabela de valores"):
                    st.dataframe(sdf, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Página: Histórico
# ---------------------------------------------------------------------------

elif page == "Histórico":
    st.header("Histórico de análises")

    db_mb = storage.db_size_bytes() / (1024 * 1024)
    max_mb = storage.MAX_DB_BYTES / (1024 * 1024)
    col_a, col_b = st.columns([3, 1])
    col_a.caption(
        f"Base de dados: {db_mb:.1f} MB (limpeza automática dispara acima de "
        f"{max_mb:.0f} MB, corre no máximo de 6 em 6h, e nunca apaga as 20 "
        f"análises mais recentes)."
    )
    if col_b.button("🧹 Limpar agora"):
        with st.spinner("A limpar..."):
            result = storage.maybe_cleanup(force=True)
        if result:
            st.success(
                f"Limpeza feita: {result['analysis_runs_deleted']} análises antigas "
                f"e {result['wallet_cache_deleted']} entradas de cache removidas. "
                f"{result['size_before_mb']} MB → {result.get('size_after_mb', '?')} MB."
            )
        else:
            st.info("Sem nada a limpar de momento.")

    runs = storage.list_analysis_runs(limit=100)
    if not runs:
        st.info("Ainda não correste nenhuma análise. Vai a 'Analisar jogo'.")
    else:
        df = pd.DataFrame(runs)
        df["run_at"] = pd.to_datetime(df["run_at"], unit="s")
        st.dataframe(
            df[["id", "event_title", "run_at", "n_combos", "n_rows", "total_volume_usdc"]],
            use_container_width=True, hide_index=True,
        )
        run_id = st.number_input("Ver detalhe do ID", min_value=0, step=1)
        if st.button("Carregar detalhe") and run_id:
            run = storage.get_analysis_run(int(run_id))
            if run:
                render_combo_results(run["rows"], run["event_title"])
            else:
                st.error("ID não encontrado.")

# ---------------------------------------------------------------------------
# Página: Watchlist
# ---------------------------------------------------------------------------

elif page == "Watchlist":
    st.header("Watchlist")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Jogos seguidos")
        games = storage.list_watchlist_games()
        if not games:
            st.caption("Nenhum jogo na watchlist ainda.")
        for g in games:
            gc1, gc2 = st.columns([4, 1])
            gc1.write(f"**{g['title']}**  \n`{g['slug']}`")
            if gc2.button("Remover", key=f"rm_game_{g['id']}"):
                storage.remove_watchlist_game(g["slug"])
                st.rerun()

    with col2:
        st.subheader("Wallets seguidas")
        wallets = storage.list_watchlist_wallets()
        if not wallets:
            st.caption("Nenhuma wallet na watchlist ainda.")
        for w in wallets:
            wc1, wc2 = st.columns([4, 1])
            label = f" ({w['label']})" if w["label"] else ""
            wc1.write(f"`{w['wallet']}`{label}")
            if wc2.button("Remover", key=f"rm_wallet_{w['id']}"):
                storage.remove_watchlist_wallet(w["wallet"])
                st.rerun()

        st.divider()
        new_wallet = st.text_input("Adicionar wallet manualmente (0x... ou username)")
        new_label = st.text_input("Etiqueta (opcional)")
        if st.button("Adicionar wallet", disabled=not new_wallet):
            resolved = pm.resolve_wallet_input(new_wallet)
            if resolved:
                storage.add_watchlist_wallet(resolved, new_label)
                st.toast("Adicionado")
                st.rerun()
            else:
                st.error("Não consegui resolver essa wallet/username.")

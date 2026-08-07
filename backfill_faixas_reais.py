"""
backfill_faixas_reais.py
--------------------------
Reprocessa TODO o histórico já carregado de Lotofácil, Mega-Sena e Lotomania,
re-buscando cada concurso na API da Caixa e fazendo upsert no Supabase +
SQLite local — necessário porque os *_db.py ganharam colunas novas (faixas
secundárias reais: 14/13/12/11 acertos na Lotofácil, quina/quadra na
Mega-Sena, 19/18/17/16/15 acertos na Lotomania) que os concursos já
carregados não têm preenchidas.

Diferente de --init-all (que só baixa o que falta), este script baixa TUDO de
novo de propósito, porque o objetivo é atualizar linhas que já existem.

Uso:
    python backfill_faixas_reais.py --loteria lotofacil
    python backfill_faixas_reais.py --loteria megasena
    python backfill_faixas_reais.py --loteria lotomania
    python backfill_faixas_reais.py --loteria ambas   # lotofacil + megasena
    python backfill_faixas_reais.py --loteria todas   # as 3
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def baixar_tudo(fetch_concurso, montar_linha, max_workers: int, ultimo: int) -> list[dict]:
    numeros = list(range(1, ultimo + 1))
    registros = []
    falhados = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_concurso, n): n for n in numeros}
        concluidos = 0
        for future in as_completed(futures):
            n = futures[future]
            data = future.result()
            if data:
                registros.append(montar_linha(data))
            else:
                falhados.append(n)
            concluidos += 1
            if concluidos % 100 == 0 or concluidos == len(numeros):
                log(f"  {concluidos}/{len(numeros)} baixados ({len(falhados)} falha(s) até agora)")

    if falhados:
        log(f"Re-tentando {len(falhados)} concurso(s) que falharam na primeira rodada...")
        ainda_falhando = []
        for n in falhados:
            data = fetch_concurso(n)
            if data:
                registros.append(montar_linha(data))
            else:
                ainda_falhando.append(n)
        if ainda_falhando:
            log(f"[aviso] {len(ainda_falhando)} concurso(s) não baixaram mesmo após re-tentativa: "
                f"{ainda_falhando[:20]}" + (" ..." if len(ainda_falhando) > 20 else ""))

    return registros


def backfill_lotofacil():
    import lotofacil_db as db_module
    from lotofacil_coletar import fetch_concurso, MAX_WORKERS

    log("=== Lotofácil ===")
    url, _ = db_module.carregar_credenciais_supabase(".")
    service_key = db_module.carregar_credencial_service_key(".")
    supa = db_module.Database.supabase(url, service_key)
    local = db_module.Database.sqlite("lotofacil.db")

    ultimo = supa.ultimo_concurso()
    log(f"Reprocessando concursos 1 a {ultimo}...")
    registros = baixar_tudo(fetch_concurso, db_module.montar_linha, MAX_WORKERS, ultimo)

    afetados_supa = supa.inserir_sorteios(registros)
    log(f"Supabase: {afetados_supa} linha(s) upsertadas.")
    afetados_local = local.inserir_sorteios(registros)
    log(f"SQLite local: {afetados_local} linha(s) upsertadas.")

    supa.fechar()
    local.fechar()
    log("Lotofácil concluído.")


def backfill_megasena():
    import megasena_db as db_module
    from megasena_atualizar import fetch_concurso, MAX_WORKERS

    log("=== Mega-Sena ===")
    url, _ = db_module.carregar_credenciais_supabase(".")
    service_key = db_module.carregar_credencial_service_key(".")
    supa = db_module.Database.supabase(url, service_key)
    local = db_module.Database.sqlite("megasena.db")

    ultimo = supa.ultimo_concurso()
    log(f"Reprocessando concursos 1 a {ultimo}...")
    registros = baixar_tudo(fetch_concurso, db_module.montar_linha, MAX_WORKERS, ultimo)

    afetados_supa = supa.inserir_sorteios(registros)
    log(f"Supabase: {afetados_supa} linha(s) upsertadas.")
    afetados_local = local.inserir_sorteios(registros)
    log(f"SQLite local: {afetados_local} linha(s) upsertadas.")

    supa.fechar()
    local.fechar()
    log("Mega-Sena concluído.")


def backfill_lotomania():
    import lotomania_db as db_module
    from lotomania_atualizar import fetch_concurso

    log("=== Lotomania ===")
    url, _ = db_module.carregar_credenciais_supabase(".")
    service_key = db_module.carregar_credencial_service_key(".")
    supa = db_module.Database.supabase(url, service_key)
    local = db_module.Database.sqlite("lotomania.db")

    ultimo = supa.ultimo_concurso()
    log(f"Reprocessando concursos 1 a {ultimo}...")
    # max_workers reduzido (8 em vez do padrão) — Lotofácil/Mega-Sena hoje
    # mostraram throttling crescente (403) ao longo de reprocessamentos
    # sucessivos da API da Caixa; mais gentil evita repetir isso aqui.
    registros = baixar_tudo(fetch_concurso, db_module.montar_linha, 8, ultimo)

    afetados_supa = supa.inserir_sorteios(registros)
    log(f"Supabase: {afetados_supa} linha(s) upsertadas.")
    afetados_local = local.inserir_sorteios(registros)
    log(f"SQLite local: {afetados_local} linha(s) upsertadas.")

    supa.fechar()
    local.fechar()
    log("Lotomania concluído.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill das faixas secundárias reais (Lotofácil/Mega-Sena/Lotomania)")
    parser.add_argument("--loteria", choices=["lotofacil", "megasena", "lotomania", "ambas", "todas"], default="ambas")
    args = parser.parse_args()

    if args.loteria in ("lotofacil", "ambas", "todas"):
        backfill_lotofacil()
    if args.loteria in ("megasena", "ambas", "todas"):
        backfill_megasena()
    if args.loteria in ("lotomania", "todas"):
        backfill_lotomania()

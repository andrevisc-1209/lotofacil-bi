"""
lotofacil_atualizar.py
------------------------
Atualiza os sorteios da Lotofácil no Supabase (fonte primária) e no SQLite
local (fallback/backup). Reaproveita a busca na API de lotofacil_coletar.py.

Uso:
    python lotofacil_atualizar.py                  # atualiza Supabase + SQLite local
    python lotofacil_atualizar.py --init 500        # primeira carga: últimos 500 sorteios
    python lotofacil_atualizar.py --init-all        # carga total: concurso 1 até o último disponível
    python lotofacil_atualizar.py --only-local      # atualiza só o SQLite (offline)
    python lotofacil_atualizar.py --source local    # descobre o delta pelo SQLite, não pelo Supabase

Credenciais do Supabase (SUPABASE_URL, SUPABASE_SERVICE_KEY) lidas de variável
de ambiente, .env ou config.json — ver lotofacil_db.carregar_credenciais_supabase.
Precisa da chave "service_role" (não a anon) porque este script escreve dados;
sem ela (ou sem SUPABASE_URL), cai automaticamente para SQLite local com aviso.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import lotofacil_db as db_module
from lotofacil_coletar import fetch_concurso, MAX_WORKERS


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def baixar_delta(numeros: list[int]) -> list[dict]:
    """Baixa os concursos da lista em paralelo (com retry por concurso, feito
    dentro de fetch_concurso) e devolve as linhas já no formato do banco. Os
    que ainda falharem depois disso passam por uma segunda rodada serial —
    útil pra cargas grandes (--init-all): na Mega-Sena, um rate-limit passageiro
    da API da Caixa derrubou ~900 de 3040 requisições paralelas de uma vez, e
    essa segunda rodada mais devagar recuperou quase todas."""
    if not numeros:
        return []

    registros = []
    falhados = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_concurso, n): n for n in numeros}
        concluidos = 0
        for future in as_completed(futures):
            n = futures[future]
            data = future.result()
            if data:
                registros.append(db_module.montar_linha(data))
            else:
                falhados.append(n)
            concluidos += 1
            if concluidos % 50 == 0 or concluidos == len(numeros):
                log(f"  {concluidos}/{len(numeros)} concursos baixados ({len(falhados)} falha(s) até agora)")

    if falhados:
        log(f"Re-tentando {len(falhados)} concurso(s) que falharam na primeira rodada...")
        ainda_falhando = []
        for n in falhados:
            data = fetch_concurso(n)
            if data:
                registros.append(db_module.montar_linha(data))
            else:
                ainda_falhando.append(n)
        if ainda_falhando:
            log(f"[aviso] {len(ainda_falhando)} concurso(s) não baixaram mesmo após re-tentativa: {ainda_falhando[:20]}"
                + (" ..." if len(ainda_falhando) > 20 else ""))

    return registros


def conectar_supabase(pasta: str):
    """Tenta conectar no Supabase com a service_role key. Retorna None (com
    log de aviso) se as credenciais não estiverem configuradas ou a conexão falhar."""
    url, _ = db_module.carregar_credenciais_supabase(pasta)
    service_key = db_module.carregar_credencial_service_key(pasta)
    if not url or not service_key:
        log("[aviso] SUPABASE_URL/SUPABASE_SERVICE_KEY não configurados — usando SQLite local.")
        return None
    try:
        candidato = db_module.Database.supabase(url, service_key)
        candidato.ultimo_concurso()  # testa conectividade de fato
        log(f"Conectado ao Supabase ({url}).")
        return candidato
    except Exception as e:
        log(f"[aviso] Supabase indisponível ({e}) — usando SQLite local como fallback.")
        return None


def atualizar(init_qtd: int | None, init_all: bool, only_local: bool, source: str, db_path: str, pasta: str):
    db_local = db_module.Database.sqlite(db_path)

    db_supabase = None
    if not only_local and source != "local":
        db_supabase = conectar_supabase(pasta)

    origem_leitura = db_supabase or db_local
    ultimo_local = origem_leitura.ultimo_concurso()

    log("Buscando último concurso disponível na API da Caixa...")
    ultimo_api_data = fetch_concurso(None)
    if not ultimo_api_data:
        log("Falha ao consultar a API da Caixa.")
        sys.exit(1)
    ultimo_api = ultimo_api_data["numero"]

    if init_all:
        log("Carga total do histórico solicitada (--init-all).")
        existentes = set(origem_leitura.concursos_existentes())
        numeros = [n for n in range(1, ultimo_api + 1) if n not in existentes]
        log(f"Último da API: {ultimo_api} | Já no banco: {len(existentes)} | Faltando: {len(numeros)}")
        if not numeros:
            log("Banco já está completo!")
    elif ultimo_local == 0:
        if init_qtd is None:
            if sys.stdin.isatty():
                resposta = input("Banco vazio. Quantos sorteios históricos deseja importar? [500]: ").strip()
                init_qtd = int(resposta) if resposta else 500
            else:
                # ambiente não-interativo (ex: GitHub Actions) — não trava esperando input
                init_qtd = 500
                log("Banco vazio e ambiente não-interativo — importando os últimos 500 sorteios por padrão.")
        inicio = max(1, ultimo_api - init_qtd + 1)
        numeros = list(range(inicio, ultimo_api + 1))
        log(f"Importando concursos {inicio} → {ultimo_api} ({len(numeros)} sorteios)...")
    elif ultimo_api <= ultimo_local:
        log("Já está tudo atualizado — nenhum concurso novo.")
        numeros = []
    else:
        numeros = list(range(ultimo_local + 1, ultimo_api + 1))
        log(f"Baixando {len(numeros)} concurso(s) novo(s): {numeros[0]} → {numeros[-1]}...")

    registros = baixar_delta(numeros)

    if db_supabase and registros:
        try:
            inseridos_supabase = db_supabase.inserir_sorteios(registros)
            log(f"Supabase: {inseridos_supabase} sorteio(s) inserido(s).")
        except Exception as e:
            log(f"[aviso] Falha ao inserir no Supabase ({e}) — mantendo só o SQLite local atualizado.")

    if registros:
        inseridos_local = db_local.inserir_sorteios(registros)
        log(f"SQLite local: {inseridos_local} sorteio(s) inserido(s).")

    total_local = db_local.total_sorteios()
    log(f"Concluído. Banco local agora tem {total_local} sorteios "
        f"(concurso {db_local.ultimo_concurso()}).")
    if db_supabase:
        log(f"Supabase sincronizado — último concurso lá: {db_supabase.ultimo_concurso()}.")
        db_supabase.fechar()
    db_local.fechar()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Atualiza os sorteios da Lotofácil no Supabase e/ou SQLite local"
    )
    parser.add_argument("--db", default="lotofacil.db", help="Caminho do banco SQLite local (padrão: lotofacil.db)")
    parser.add_argument("--init", type=int, default=None, help="Primeira carga: quantidade de sorteios históricos a importar")
    parser.add_argument("--init-all", action="store_true",
                         help="Carga total do histórico: concurso 1 até o último disponível (pula os que já existem no banco)")
    parser.add_argument("--only-local", action="store_true", help="Não tenta o Supabase — atualiza só o SQLite local")
    parser.add_argument("--source", choices=["supabase", "local"], default="supabase",
                         help="De onde ler o 'último concurso' para descobrir o delta (padrão: supabase)")
    args = parser.parse_args()

    atualizar(args.init, args.init_all, args.only_local, args.source, args.db, ".")

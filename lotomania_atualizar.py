"""
lotomania_atualizar.py
------------------------
Coleta e atualiza os sorteios da Lotomania no Supabase (fonte primária) e no
SQLite local (fallback/backup) — mesmo padrão de lotofacil_atualizar.py e
megasena_atualizar.py, usando as MESMAS credenciais do Supabase da Lotofácil
(só a tabela muda, ver lotomania_db.TABELA).

Uso:
    python lotomania_atualizar.py                  # atualiza Supabase + SQLite local (só os novos)
    python lotomania_atualizar.py --init-all        # carga total: concurso 1 até o último disponível
    python lotomania_atualizar.py --only-local      # atualiza só o SQLite (offline)
    python lotomania_atualizar.py --source local    # descobre o delta pelo SQLite, não pelo Supabase

Credenciais do Supabase (SUPABASE_URL, SUPABASE_SERVICE_KEY) lidas de variável
de ambiente, .env ou config.json — ver lotofacil_db.carregar_credenciais_supabase
(reaproveitado por lotomania_db, que só troca a tabela alvo).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

import lotomania_db as db_module

BASE_URL = "https://servicebus2.caixa.gov.br/portaldeloterias/api/lotomania"
MAX_WORKERS = 20
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def fetch_concurso(numero: int | None) -> dict | None:
    """Busca um concurso específico (ou o último, se numero for None). Retorna
    None se falhar após retries — quem chama decide se re-tenta depois."""
    url = f"{BASE_URL}/{numero}" if numero else BASE_URL
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, Exception) as e:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                print(f"  [ERRO] concurso {numero}: {e}", file=sys.stderr)
                return None


def baixar_lote(numeros: list[int]) -> list[dict]:
    """Baixa os concursos da lista em paralelo (com retry por concurso, feito
    dentro de fetch_concurso) e devolve as linhas já no formato do banco. Os
    que ainda falharem depois disso passam por uma segunda rodada serial —
    mesmo padrão já usado (e testado contra rate-limit real da API da Caixa)
    em megasena_atualizar.py e lotofacil_atualizar.py."""
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
            if concluidos % 100 == 0 or concluidos == len(numeros):
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
        log(f"Conectado ao Supabase ({url}), tabela {db_module.TABELA}.")
        return candidato
    except Exception as e:
        log(f"[aviso] Supabase indisponível ({e}) — usando SQLite local como fallback.")
        return None


def atualizar(init_all: bool, only_local: bool, source: str, db_path: str, pasta: str):
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
        numeros = list(range(1, ultimo_api + 1))
        log(f"Banco vazio — carga total: concursos 1 → {ultimo_api} ({len(numeros)} sorteios). "
            f"Pode levar alguns minutos...")
    elif ultimo_api <= ultimo_local:
        log("Já está tudo atualizado — nenhum concurso novo.")
        numeros = []
    else:
        numeros = list(range(ultimo_local + 1, ultimo_api + 1))
        log(f"Baixando {len(numeros)} concurso(s) novo(s): {numeros[0]} → {numeros[-1]}...")

    registros = baixar_lote(numeros)

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
        description="Atualiza os sorteios da Lotomania no Supabase e/ou SQLite local"
    )
    parser.add_argument("--db", default="lotomania.db", help="Caminho do banco SQLite local (padrão: lotomania.db)")
    parser.add_argument("--init-all", action="store_true",
                         help="Carga total do histórico: concurso 1 até o último disponível (pula os que já existem)")
    parser.add_argument("--only-local", action="store_true", help="Não tenta o Supabase — atualiza só o SQLite local")
    parser.add_argument("--source", choices=["supabase", "local"], default="supabase",
                         help="De onde ler o 'último concurso' para descobrir o delta (padrão: supabase)")
    args = parser.parse_args()

    atualizar(args.init_all, args.only_local, args.source, args.db, ".")

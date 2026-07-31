"""
lotofacil_migrar.py
---------------------
Migra os sorteios do SQLite local para o Supabase, em lotes de 100 (INSERT
com resolução "ignore-duplicates" — não duplica quem já estiver lá).

Uso:
    python lotofacil_migrar.py --from sqlite --to supabase
    python lotofacil_migrar.py --db lotofacil.db --lote 50

Credenciais (SUPABASE_URL, SUPABASE_SERVICE_KEY) lidas de variável de
ambiente, .env ou config.json — ver lotofacil_db.carregar_credenciais_supabase.
Precisa da chave "service_role" (não a anon) porque é uma escrita.
"""

from __future__ import annotations

import argparse
import sys

from lotofacil_db import Database, carregar_credenciais_supabase, carregar_credencial_service_key


def migrar(db_sqlite_path: str, lote: int):
    origem = Database.sqlite(db_sqlite_path)
    registros = origem.carregar_todos()
    origem.fechar()

    if not registros:
        print(f"Nenhum sorteio encontrado em '{db_sqlite_path}'. Nada para migrar.")
        return

    url, _ = carregar_credenciais_supabase()
    service_key = carregar_credencial_service_key()
    if not url or not service_key:
        print(
            "SUPABASE_URL e SUPABASE_SERVICE_KEY precisam estar configurados "
            "(variável de ambiente, .env ou config.json) para migrar dados.\n"
            "A service_role key fica em Supabase > Settings > API > 'service_role'.",
            file=sys.stderr,
        )
        sys.exit(1)

    destino = Database.supabase(url, service_key)
    ja_no_destino = destino.ultimo_concurso()
    print(f"Origem: {len(registros)} sorteios em '{db_sqlite_path}'.")
    print(f"Destino: Supabase ({url}) — último concurso já lá: {ja_no_destino or 'nenhum'}.\n")

    total_lotes = (len(registros) + lote - 1) // lote
    inseridos_total = 0
    for i in range(0, len(registros), lote):
        num_lote = i // lote + 1
        pedaco = registros[i:i + lote]
        print(f"Migrando lote {num_lote}/{total_lotes} ({len(pedaco)} registros)...")
        inseridos_total += destino.inserir_sorteios(pedaco)

    ignorados = len(registros) - inseridos_total
    destino.fechar()
    print(f"\n✓ {inseridos_total} sorteios migrados. {ignorados} ignorados (já existiam).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migra sorteios do SQLite local para o Supabase")
    parser.add_argument("--from", dest="origem", choices=["sqlite"], default="sqlite")
    parser.add_argument("--to", dest="destino", choices=["supabase"], default="supabase")
    parser.add_argument("--db", default="lotofacil.db", help="Caminho do banco SQLite de origem")
    parser.add_argument("--lote", type=int, default=100, help="Tamanho do lote de inserção (padrão: 100)")
    args = parser.parse_args()

    migrar(args.db, args.lote)

"""
duplasena_db.py
----------------
Camada de acesso a dados da Dupla Sena — mesmo padrão de megasena_db.py e
lotomania_db.py (dois backends intercambiáveis: SQLite local e Supabase via
REST/PostgREST puro), com o diferencial de guardar DOIS sorteios por
concurso (d01-d06 = 1º sorteio, s01-s06 = 2º sorteio) e 8 faixas de prêmio
(3/4/5/6 acertos × 1ª/2ª rodada). As credenciais do Supabase são as MESMAS
do projeto da Lotofácil (SUPABASE_URL/SUPABASE_ANON_KEY/SUPABASE_SERVICE_KEY
já configuradas em .env) — só a tabela muda.

Uso:
    from duplasena_db import Database

    db = Database.sqlite("duplasena.db")
    db = Database.supabase(url="https://xxxx.supabase.co", key="eyJ...")

    db.ultimo_concurso()          -> int
    db.concursos_existentes()     -> list[int]
    db.inserir_sorteios([...])    -> int (quantas linhas afetou)
    db.carregar_todos()           -> list[dict]
    db.exportar_csv("saida.csv")
    db.exportar_json("saida.json")
    db.fechar()
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# credenciais lidas do mesmo jeito que a Lotofácil (env var > .env > config.json)
from lotofacil_db import carregar_credenciais_supabase, carregar_credencial_service_key  # noqa: F401

TABELA = "duplasena_sorteios"

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS duplasena_sorteios (
    concurso      INTEGER PRIMARY KEY,
    data          TEXT NOT NULL,          -- formato ISO: YYYY-MM-DD
    data_br       TEXT,                   -- formato original: DD/MM/AAAA
    acumulado     INTEGER DEFAULT 0,
    d01 INTEGER, d02 INTEGER, d03 INTEGER, d04 INTEGER, d05 INTEGER, d06 INTEGER,
    s01 INTEGER, s02 INTEGER, s03 INTEGER, s04 INTEGER, s05 INTEGER, s06 INTEGER,
    valor_sena1 REAL, ganhadores_sena1 INTEGER,
    valor_quina1 REAL, ganhadores_quina1 INTEGER,
    valor_quadra1 REAL, ganhadores_quadra1 INTEGER,
    valor_terno1 REAL, ganhadores_terno1 INTEGER,
    valor_sena2 REAL, ganhadores_sena2 INTEGER,
    valor_quina2 REAL, ganhadores_quina2 INTEGER,
    valor_quadra2 REAL, ganhadores_quadra2 INTEGER,
    valor_terno2 REAL, ganhadores_terno2 INTEGER
);
"""

# faixa 1-4 = 1ª rodada (6/5/4/3 acertos), faixa 5-8 = 2ª rodada (6/5/4/3) —
# confirmado via API real que é a única forma confiável de distinguir as
# rodadas: descricaoFaixa é IDÊNTICA ("6 acertos") nas duas, sem nenhum
# marcador de rodada no texto. A estrutura de faixas também mudou ao longo
# da história (concurso 1 tinha só 4 faixas no total — sem quina1/quadra1
# nem faixa de 3 acertos em nenhuma rodada; a faixa de 3 acertos ("terno")
# só passou a existir bem mais tarde) — faixas ausentes ficam NULL no banco,
# não são inventadas como zero.
NOME_FAIXA_POR_NUMERO = {
    1: "sena1", 2: "quina1", 3: "quadra1", 4: "terno1",
    5: "sena2", 6: "quina2", 7: "quadra2", 8: "terno2",
}

COLUNAS_PREMIOS = [c for nome in NOME_FAIXA_POR_NUMERO.values() for c in (f"valor_{nome}", f"ganhadores_{nome}")]

COLUNAS = (
    ["concurso", "data", "data_br", "acumulado"]
    + [f"d{i:02d}" for i in range(1, 7)]
    + [f"s{i:02d}" for i in range(1, 7)]
    + COLUNAS_PREMIOS
)


# ─── conversão API da Caixa → linha do banco (comum aos dois backends) ───────

def data_br_para_iso(data_br: str) -> str:
    return datetime.strptime(data_br, "%d/%m/%Y").strftime("%Y-%m-%d")

def montar_linha(data_api: dict) -> dict:
    """Converte o JSON retornado pela API da Caixa (endpoint /duplasena/{n})
    para o formato de linha do banco. listaDezenas = 1º sorteio,
    listaDezenasSegundoSorteio = 2º sorteio (confirmado via API real — nome
    exato do campo). Faixas identificadas pelo número (ver NOME_FAIXA_POR_NUMERO)."""
    dezenas1 = sorted(int(d) for d in (data_api.get("listaDezenas") or []))
    dezenas2 = sorted(int(d) for d in (data_api.get("listaDezenasSegundoSorteio") or []))
    data_br = data_api.get("dataApuracao")

    premios = {}
    for faixa in data_api.get("listaRateioPremio") or []:
        n = faixa.get("faixa")
        if n in NOME_FAIXA_POR_NUMERO:
            nome = NOME_FAIXA_POR_NUMERO[n]
            premios[f"valor_{nome}"] = faixa.get("valorPremio")
            premios[f"ganhadores_{nome}"] = faixa.get("numeroDeGanhadores")

    linha = {
        "concurso": data_api.get("numero"),
        "data": data_br_para_iso(data_br) if data_br else None,
        "data_br": data_br,
        "acumulado": 1 if data_api.get("acumulado") else 0,
        **{c: premios.get(c) for c in COLUNAS_PREMIOS},
    }
    for i, d in enumerate(dezenas1, start=1):
        linha[f"d{i:02d}"] = d
    for i, d in enumerate(dezenas2, start=1):
        linha[f"s{i:02d}"] = d
    return linha


# ─── backend SQLite ────────────────────────────────────────────────────────

class _SQLiteBackend:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(SCHEMA_SQLITE)
        self.conn.commit()

    def ultimo_concurso(self) -> int:
        row = self.conn.execute(f"SELECT MAX(concurso) FROM {TABELA}").fetchone()
        return row[0] or 0

    def concursos_existentes(self) -> list[int]:
        cur = self.conn.execute(f"SELECT concurso FROM {TABELA}")
        return [row[0] for row in cur.fetchall()]

    def inserir_sorteios(self, registros: list[dict]) -> int:
        """Upsert (INSERT ... ON CONFLICT DO UPDATE): reprocessar um concurso
        já existente atualiza a linha em vez de ser ignorado."""
        colunas_sql = ", ".join(COLUNAS)
        placeholders = ", ".join("?" for _ in COLUNAS)
        update_sql = ", ".join(f"{c}=excluded.{c}" for c in COLUNAS if c != "concurso")
        afetados = 0
        for dados in registros:
            valores = [dados.get(c) for c in COLUNAS]
            cursor = self.conn.execute(
                f"INSERT INTO {TABELA} ({colunas_sql}) VALUES ({placeholders}) "
                f"ON CONFLICT(concurso) DO UPDATE SET {update_sql}",
                valores,
            )
            if cursor.rowcount > 0:
                afetados += 1
        self.conn.commit()
        return afetados

    def carregar_todos(self) -> list[dict]:
        row_factory_original = self.conn.row_factory
        self.conn.row_factory = sqlite3.Row
        try:
            cur = self.conn.execute(f"SELECT * FROM {TABELA} ORDER BY concurso ASC")
            return [dict(row) for row in cur.fetchall()]
        finally:
            self.conn.row_factory = row_factory_original

    def fechar(self):
        self.conn.close()


# ─── backend Supabase (REST/PostgREST puro, via urllib) ──────────────────────

class _SupabaseBackend:
    LOTE = 100

    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.key = key

    def _request(self, method: str, path: str, headers: dict | None = None, body=None):
        url = f"{self.url}/rest/v1/{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        base_headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if headers:
            base_headers.update(headers)
        req = Request(url, data=data, method=method, headers=base_headers)
        try:
            with urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except HTTPError as e:
            raw = e.read()
            try:
                corpo = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                corpo = raw.decode("utf-8", errors="replace")
            return e.code, corpo
        except URLError as e:
            raise RuntimeError(f"Não foi possível conectar ao Supabase: {e.reason}") from e

    def ultimo_concurso(self) -> int:
        status, corpo = self._request("GET", f"{TABELA}?select=concurso&order=concurso.desc&limit=1")
        if status >= 400:
            raise RuntimeError(f"Supabase respondeu {status} ao buscar último concurso: {corpo}")
        return corpo[0]["concurso"] if corpo else 0

    def concursos_existentes(self) -> list[int]:
        TAMANHO_PAGINA = 1000
        todos = []
        offset = 0
        while True:
            status, corpo = self._request(
                "GET", f"{TABELA}?select=concurso&order=concurso.asc",
                headers={"Range-Unit": "items", "Range": f"{offset}-{offset + TAMANHO_PAGINA - 1}"},
            )
            if status >= 400:
                raise RuntimeError(f"Supabase respondeu {status} ao listar concursos existentes: {corpo}")
            pagina = corpo or []
            todos.extend(row["concurso"] for row in pagina)
            if len(pagina) < TAMANHO_PAGINA:
                break
            offset += TAMANHO_PAGINA
        return todos

    @staticmethod
    def _normalizar(dados: dict) -> dict:
        linha = {c: dados.get(c) for c in COLUNAS}
        linha["acumulado"] = bool(linha.get("acumulado"))
        return linha

    def inserir_sorteios(self, registros: list[dict]) -> int:
        """Upsert (resolution=merge-duplicates): reprocessar um concurso já
        existente atualiza a linha em vez de ser ignorado."""
        inseridos = 0
        for i in range(0, len(registros), self.LOTE):
            lote = [self._normalizar(r) for r in registros[i:i + self.LOTE]]
            status, corpo = self._request(
                "POST", f"{TABELA}?on_conflict=concurso",
                headers={"Prefer": "resolution=merge-duplicates,return=representation"},
                body=lote,
            )
            if status >= 400:
                raise RuntimeError(f"Supabase respondeu {status} ao inserir lote: {corpo}")
            inseridos += len(corpo) if isinstance(corpo, list) else 0
        return inseridos

    def carregar_todos(self) -> list[dict]:
        """Pagina em blocos de 1000 — o PostgREST limita select=* sem range a
        1000 linhas por padrão (db-max-rows)."""
        TAMANHO_PAGINA = 1000
        todos = []
        offset = 0
        while True:
            status, corpo = self._request(
                "GET", f"{TABELA}?select=*&order=concurso.asc",
                headers={"Range-Unit": "items", "Range": f"{offset}-{offset + TAMANHO_PAGINA - 1}"},
            )
            if status >= 400:
                raise RuntimeError(f"Supabase respondeu {status} ao carregar sorteios: {corpo}")
            pagina = corpo or []
            todos.extend(pagina)
            if len(pagina) < TAMANHO_PAGINA:
                break
            offset += TAMANHO_PAGINA
        return todos

    def fechar(self):
        pass  # sem conexão persistente — cada chamada é uma requisição HTTP


# ─── interface pública ────────────────────────────────────────────────────

class Database:
    """Interface comum a SQLite e Supabase. Use Database.sqlite(...) ou
    Database.supabase(...) para instanciar — não o construtor direto."""

    def __init__(self, backend):
        self._backend = backend

    @classmethod
    def sqlite(cls, db_path: str) -> "Database":
        return cls(_SQLiteBackend(db_path))

    @classmethod
    def supabase(cls, url: str, key: str) -> "Database":
        return cls(_SupabaseBackend(url, key))

    def ultimo_concurso(self) -> int:
        return self._backend.ultimo_concurso()

    def concursos_existentes(self) -> list[int]:
        return self._backend.concursos_existentes()

    def inserir_sorteios(self, lista_de_dicts: list[dict]) -> int:
        return self._backend.inserir_sorteios(lista_de_dicts)

    def carregar_todos(self) -> list[dict]:
        return self._backend.carregar_todos()

    def total_sorteios(self) -> int:
        return len(self.carregar_todos())

    def exportar_csv(self, path: str) -> int:
        registros = self.carregar_todos()
        fieldnames = ["concurso", "data", "acumulado"] + [f"d{i:02d}" for i in range(1, 7)] + [f"s{i:02d}" for i in range(1, 7)]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in registros:
                linha = {"concurso": r["concurso"], "data": r.get("data_br") or r["data"], "acumulado": r.get("acumulado")}
                for i in range(1, 7):
                    linha[f"d{i:02d}"] = r[f"d{i:02d}"]
                    linha[f"s{i:02d}"] = r[f"s{i:02d}"]
                writer.writerow(linha)
        return len(registros)

    def exportar_json(self, path: str) -> int:
        registros = self.carregar_todos()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(registros, f, ensure_ascii=False, indent=2, default=str)
        return len(registros)

    def fechar(self):
        self._backend.fechar()

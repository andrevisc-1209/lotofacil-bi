"""
megasena_db.py
----------------
Camada de acesso a dados da Mega-Sena — mesmo padrão de lotofacil_db.py
(dois backends intercambiáveis: SQLite local e Supabase via REST/PostgREST
puro), só que com 6 dezenas (01-60) em vez de 15 (01-25) e apontando para a
tabela megasena_sorteios em vez de sorteios. As credenciais do Supabase são
as MESMAS do projeto da Lotofácil (SUPABASE_URL/SUPABASE_ANON_KEY/
SUPABASE_SERVICE_KEY já configuradas em .env) — só a tabela muda.

Uso:
    from megasena_db import Database

    db = Database.sqlite("megasena.db")
    db = Database.supabase(url="https://xxxx.supabase.co", key="eyJ...")

    db.ultimo_concurso()          -> int
    db.inserir_sorteios([...])    -> int (quantos inseriu de fato)
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

TABELA = "megasena_sorteios"

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS megasena_sorteios (
    concurso      INTEGER PRIMARY KEY,
    data          TEXT NOT NULL,          -- formato ISO: YYYY-MM-DD
    data_br       TEXT,                   -- formato original: DD/MM/AAAA
    acumulado     INTEGER DEFAULT 0,
    valor_premio  REAL,
    ganhadores    INTEGER,
    d01 INTEGER, d02 INTEGER, d03 INTEGER,
    d04 INTEGER, d05 INTEGER, d06 INTEGER
);
"""

# faixas 2 (quina, 5 acertos) e 3 (quadra, 4 acertos) — adicionadas depois do
# schema original. Confirmado via API real que são sempre variáveis (rateio
# por sorteio, nunca fixas), então cada sorteio guarda seu próprio valor —
# nunca usar uma constante fixa pra elas no código.
COLUNAS_FAIXAS_SECUNDARIAS = ["valor_quina", "ganhadores_quina", "valor_quadra", "ganhadores_quadra"]

COLUNAS = ["concurso", "data", "data_br", "acumulado", "valor_premio", "ganhadores"] + [
    f"d{i:02d}" for i in range(1, 7)
] + COLUNAS_FAIXAS_SECUNDARIAS


# ─── conversão API da Caixa → linha do banco (comum aos dois backends) ───────

def data_br_para_iso(data_br: str) -> str:
    return datetime.strptime(data_br, "%d/%m/%Y").strftime("%Y-%m-%d")

def montar_linha(data_api: dict) -> dict:
    """Converte o JSON retornado pela API da Caixa (endpoint /megasena/{n})
    para o formato de linha do banco. faixa 1 = sena (6 acertos, prêmio maior),
    igual à faixa 1 da Lotofácil ser a de 15 acertos."""
    dezenas = sorted(int(d) for d in (data_api.get("listaDezenas") or []))
    data_br = data_api.get("dataApuracao")

    valor_premio = None
    ganhadores = None
    valor_quina = ganhadores_quina = None
    valor_quadra = ganhadores_quadra = None
    for faixa in data_api.get("listaRateioPremio") or []:
        n = faixa.get("faixa")
        if n == 1:
            valor_premio = faixa.get("valorPremio")
            ganhadores = faixa.get("numeroDeGanhadores")
        elif n == 2:
            valor_quina = faixa.get("valorPremio")
            ganhadores_quina = faixa.get("numeroDeGanhadores")
        elif n == 3:
            valor_quadra = faixa.get("valorPremio")
            ganhadores_quadra = faixa.get("numeroDeGanhadores")

    linha = {
        "concurso": data_api.get("numero"),
        "data": data_br_para_iso(data_br) if data_br else None,
        "data_br": data_br,
        "acumulado": 1 if data_api.get("acumulado") else 0,
        "valor_premio": valor_premio,
        "ganhadores": ganhadores,
        "valor_quina": valor_quina,
        "ganhadores_quina": ganhadores_quina,
        "valor_quadra": valor_quadra,
        "ganhadores_quadra": ganhadores_quadra,
    }
    for i, d in enumerate(dezenas, start=1):
        linha[f"d{i:02d}"] = d
    return linha


# ─── backend SQLite ────────────────────────────────────────────────────────

class _SQLiteBackend:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(SCHEMA_SQLITE)
        self._garantir_colunas_faixas_secundarias()
        self.conn.commit()

    def _garantir_colunas_faixas_secundarias(self):
        """valor_quina/ganhadores_quina/valor_quadra/ganhadores_quadra foram
        adicionadas depois do schema original — ALTER TABLE idempotente pra
        bancos já existentes."""
        colunas_atuais = {row[1] for row in self.conn.execute(f"PRAGMA table_info({TABELA})")}
        for coluna in COLUNAS_FAIXAS_SECUNDARIAS:
            if coluna not in colunas_atuais:
                tipo = "INTEGER" if coluna.startswith("ganhadores_") else "REAL"
                self.conn.execute(f"ALTER TABLE {TABELA} ADD COLUMN {coluna} {tipo}")

    def ultimo_concurso(self) -> int:
        row = self.conn.execute(f"SELECT MAX(concurso) FROM {TABELA}").fetchone()
        return row[0] or 0

    def inserir_sorteios(self, registros: list[dict]) -> int:
        """Upsert (INSERT ... ON CONFLICT DO UPDATE): reprocessar um concurso
        já existente atualiza a linha em vez de ser ignorado — necessário pro
        backfill das faixas secundárias sobre o histórico já carregado."""
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

    @staticmethod
    def _normalizar(dados: dict) -> dict:
        linha = {c: dados.get(c) for c in COLUNAS}
        linha["acumulado"] = bool(linha.get("acumulado"))
        return linha

    def inserir_sorteios(self, registros: list[dict]) -> int:
        """Upsert (resolution=merge-duplicates): reprocessar um concurso já
        existente atualiza a linha em vez de ser ignorado — necessário pro
        backfill das faixas secundárias sobre o histórico já carregado."""
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
        1000 linhas por padrão (db-max-rows), e a Mega-Sena (3000+ sorteios)
        estoura esse limite numa página só (a Lotofácil nunca bateu nisso
        porque tem menos de 1000 sorteios)."""
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

    def inserir_sorteios(self, lista_de_dicts: list[dict]) -> int:
        return self._backend.inserir_sorteios(lista_de_dicts)

    def carregar_todos(self) -> list[dict]:
        return self._backend.carregar_todos()

    def total_sorteios(self) -> int:
        return len(self.carregar_todos())

    def exportar_csv(self, path: str) -> int:
        registros = self.carregar_todos()
        fieldnames = ["concurso", "data", "acumulado", "valor_premio_1", "ganhadores_1"] + [
            f"d{i:02d}" for i in range(1, 7)
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in registros:
                linha = {
                    "concurso": r["concurso"],
                    "data": r.get("data_br") or r["data"],
                    "acumulado": r.get("acumulado"),
                    "valor_premio_1": r.get("valor_premio"),
                    "ganhadores_1": r.get("ganhadores"),
                }
                for i in range(1, 7):
                    linha[f"d{i:02d}"] = r[f"d{i:02d}"]
                writer.writerow(linha)
        return len(registros)

    def exportar_json(self, path: str) -> int:
        registros = self.carregar_todos()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(registros, f, ensure_ascii=False, indent=2, default=str)
        return len(registros)

    def fechar(self):
        self._backend.fechar()

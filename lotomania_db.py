"""
lotomania_db.py
----------------
Camada de acesso a dados da Lotomania — mesmo padrão de lotofacil_db.py e
megasena_db.py (dois backends intercambiáveis: SQLite local e Supabase via
REST/PostgREST puro), só que com 20 dezenas sorteadas (01-100) e apontando
para a tabela lotomania_sorteios. As credenciais do Supabase são as MESMAS
do projeto da Lotofácil (SUPABASE_URL/SUPABASE_ANON_KEY/SUPABASE_SERVICE_KEY
já configuradas em .env) — só a tabela muda.

Uso:
    from lotomania_db import Database

    db = Database.sqlite("lotomania.db")
    db = Database.supabase(url="https://xxxx.supabase.co", key="eyJ...")

    db.ultimo_concurso()          -> int
    db.concursos_existentes()     -> list[int]
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

TABELA = "lotomania_sorteios"

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS lotomania_sorteios (
    concurso      INTEGER PRIMARY KEY,
    data          TEXT NOT NULL,          -- formato ISO: YYYY-MM-DD
    data_br       TEXT,                   -- formato original: DD/MM/AAAA
    acumulado     INTEGER DEFAULT 0,
    valor_premio  REAL,
    ganhadores    INTEGER,
    d01 INTEGER, d02 INTEGER, d03 INTEGER, d04 INTEGER, d05 INTEGER,
    d06 INTEGER, d07 INTEGER, d08 INTEGER, d09 INTEGER, d10 INTEGER,
    d11 INTEGER, d12 INTEGER, d13 INTEGER, d14 INTEGER, d15 INTEGER,
    d16 INTEGER, d17 INTEGER, d18 INTEGER, d19 INTEGER, d20 INTEGER
);
"""

# faixas 2-6 (19/18/17/16/15 acertos) — adicionadas depois do schema
# original, mesmo motivo da faixa 7 (surpresa): todos os prêmios da
# Lotomania são variáveis (rateio por sorteio), nunca fixos.
NOME_FAIXA = {2: "dezenove", 3: "dezoito", 4: "dezessete", 5: "dezesseis", 6: "quinze"}
COLUNAS_FAIXAS_SECUNDARIAS = [c for nome in NOME_FAIXA.values() for c in (f"valor_{nome}", f"ganhadores_{nome}")]

COLUNAS = (
    ["concurso", "data", "data_br", "acumulado", "valor_premio", "ganhadores"]
    + [f"d{i:02d}" for i in range(1, 21)]
    + ["valor_surpresa", "ganhadores_surpresa"]
    + COLUNAS_FAIXAS_SECUNDARIAS
)


# ─── conversão API da Caixa → linha do banco (comum aos dois backends) ───────

def data_br_para_iso(data_br: str) -> str:
    return datetime.strptime(data_br, "%d/%m/%Y").strftime("%Y-%m-%d")

def montar_linha(data_api: dict) -> dict:
    """Converte o JSON retornado pela API da Caixa (endpoint /lotomania/{n})
    para o formato de linha do banco. Faixa 1 = "vinte" (20 acertos, prêmio
    maior). Faixas 2-6 = dezenove/dezoito/dezessete/dezesseis/quinze. A
    Lotomania tem uma 7ª faixa única no Brasil, "surpresa" (0 acertos) —
    confirmado via API real que ela é sempre listaRateioPremio com faixa=7 e
    descricaoFaixa="0 acertos", então usamos o número da faixa (estável) em
    vez de casar a descrição por texto. TODOS os prêmios da Lotomania são
    variáveis (rateio por sorteio) — nenhuma faixa usa valor fixo."""
    dezenas = sorted(int(d) for d in (data_api.get("listaDezenas") or []))
    data_br = data_api.get("dataApuracao")

    valor_premio = None
    ganhadores = None
    valor_surpresa = None
    ganhadores_surpresa = None
    secundarias = {}
    for faixa in data_api.get("listaRateioPremio") or []:
        n = faixa.get("faixa")
        if n == 1:
            valor_premio = faixa.get("valorPremio")
            ganhadores = faixa.get("numeroDeGanhadores")
        elif n == 7:
            valor_surpresa = faixa.get("valorPremio")
            ganhadores_surpresa = faixa.get("numeroDeGanhadores")
        elif n in NOME_FAIXA:
            nome = NOME_FAIXA[n]
            secundarias[f"valor_{nome}"] = faixa.get("valorPremio")
            secundarias[f"ganhadores_{nome}"] = faixa.get("numeroDeGanhadores")

    linha = {
        "concurso": data_api.get("numero"),
        "data": data_br_para_iso(data_br) if data_br else None,
        "data_br": data_br,
        "acumulado": 1 if data_api.get("acumulado") else 0,
        "valor_premio": valor_premio,
        "ganhadores": ganhadores,
        "valor_surpresa": valor_surpresa,
        "ganhadores_surpresa": ganhadores_surpresa,
        **{c: secundarias.get(c) for c in COLUNAS_FAIXAS_SECUNDARIAS},
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
        """valor_surpresa/ganhadores_surpresa e as faixas 2-6 (dezenove...
        quinze) foram adicionadas depois do schema original — ALTER TABLE
        idempotente pra bancos já existentes."""
        colunas_atuais = {row[1] for row in self.conn.execute(f"PRAGMA table_info({TABELA})")}
        for coluna in ["valor_surpresa", "ganhadores_surpresa"] + COLUNAS_FAIXAS_SECUNDARIAS:
            if coluna not in colunas_atuais:
                tipo = "INTEGER" if coluna.startswith("ganhadores_") else "REAL"
                self.conn.execute(f"ALTER TABLE {TABELA} ADD COLUMN {coluna} {tipo}")

    def ultimo_concurso(self) -> int:
        row = self.conn.execute(f"SELECT MAX(concurso) FROM {TABELA}").fetchone()
        return row[0] or 0

    def concursos_existentes(self) -> list[int]:
        cur = self.conn.execute(f"SELECT concurso FROM {TABELA}")
        return [row[0] for row in cur.fetchall()]

    def inserir_sorteios(self, registros: list[dict]) -> int:
        """Upsert (INSERT ... ON CONFLICT DO UPDATE): reprocessar um concurso
        já existente atualiza a linha em vez de ser ignorado — necessário pro
        backfill das faixas secundárias sobre o histórico já carregado."""
        placeholders = ", ".join("?" for _ in COLUNAS)
        colunas_sql = ", ".join(COLUNAS)
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
        """Só os números de concurso — usado por --init-all pra pular os que
        já existem no download, em vez de rebaixar tudo. Paginado pela mesma
        razão de carregar_todos()."""
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
        1000 linhas por padrão (db-max-rows), e a Lotomania (~2700 sorteios)
        estoura esse limite numa página só."""
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
        fieldnames = ["concurso", "data", "acumulado", "valor_premio_1", "ganhadores_1"] + [
            f"d{i:02d}" for i in range(1, 21)
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
                for i in range(1, 21):
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

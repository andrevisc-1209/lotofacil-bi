"""
lotofacil_db.py
----------------
Camada de acesso a dados da Lotofácil, com dois backends intercambiáveis:
SQLite local e Supabase (Postgres gerenciado, via REST/PostgREST puro — sem
precisar instalar supabase-py). Os scripts de negócio (lotofacil_atualizar.py,
lotofacil_migrar.py, lotofacil_bi.py) usam só a interface comum de Database e
não sabem (nem precisam saber) qual backend está por trás.

Uso:
    from lotofacil_db import Database

    db = Database.sqlite("lotofacil.db")
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
import os
import sqlite3
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS sorteios (
    concurso      INTEGER PRIMARY KEY,
    data          TEXT NOT NULL,          -- formato ISO: YYYY-MM-DD
    data_br       TEXT,                   -- formato original: DD/MM/AAAA
    acumulado     INTEGER DEFAULT 0,
    valor_premio  REAL,
    ganhadores    INTEGER,
    d01 INTEGER, d02 INTEGER, d03 INTEGER, d04 INTEGER, d05 INTEGER,
    d06 INTEGER, d07 INTEGER, d08 INTEGER, d09 INTEGER, d10 INTEGER,
    d11 INTEGER, d12 INTEGER, d13 INTEGER, d14 INTEGER, d15 INTEGER,
    s01 INTEGER, s02 INTEGER, s03 INTEGER, s04 INTEGER, s05 INTEGER,
    s06 INTEGER, s07 INTEGER, s08 INTEGER, s09 INTEGER, s10 INTEGER,
    s11 INTEGER, s12 INTEGER, s13 INTEGER, s14 INTEGER, s15 INTEGER
);
"""

# faixas 2-5 (14/13/12/11 acertos) — adicionadas depois do schema original.
# Confirmado via API real que essas faixas NÃO são fixas ao longo da história
# (ex.: 13 acertos pagava R$20 no concurso 1500, R$30 no 3000, R$35 a partir
# do ~3500) — por isso cada sorteio guarda seu próprio valor, em vez de usar
# uma constante única no código.
COLUNAS_FAIXAS_SECUNDARIAS = [
    "valor_premio_14", "ganhadores_14",
    "valor_premio_13", "ganhadores_13",
    "valor_premio_12", "ganhadores_12",
    "valor_premio_11", "ganhadores_11",
]

COLUNAS = (
    ["concurso", "data", "data_br", "acumulado", "valor_premio", "ganhadores"]
    + [f"d{i:02d}" for i in range(1, 16)]
    + [f"s{i:02d}" for i in range(1, 16)]
    + COLUNAS_FAIXAS_SECUNDARIAS
)


# ─── conversão API da Caixa → linha do banco (comum aos dois backends) ───────

def data_br_para_iso(data_br: str) -> str:
    return datetime.strptime(data_br, "%d/%m/%Y").strftime("%Y-%m-%d")

def montar_linha(data_api: dict) -> dict:
    """Converte o JSON retornado pela API da Caixa para o formato de linha do banco."""
    dezenas = sorted(int(d) for d in (data_api.get("listaDezenas") or []))
    ordem = [int(d) for d in (data_api.get("dezenasSorteadasOrdemSorteio") or [])]
    data_br = data_api.get("dataApuracao")

    valor_premio = None
    ganhadores = None
    faixas_secundarias = {}
    NOME_FAIXA = {2: "14", 3: "13", 4: "12", 5: "11"}
    for faixa in data_api.get("listaRateioPremio") or []:
        n = faixa.get("faixa")
        if n == 1:
            valor_premio = faixa.get("valorPremio")
            ganhadores = faixa.get("numeroDeGanhadores")
        elif n in NOME_FAIXA:
            nome = NOME_FAIXA[n]
            faixas_secundarias[f"valor_premio_{nome}"] = faixa.get("valorPremio")
            faixas_secundarias[f"ganhadores_{nome}"] = faixa.get("numeroDeGanhadores")

    linha = {
        "concurso": data_api.get("numero"),
        "data": data_br_para_iso(data_br) if data_br else None,
        "data_br": data_br,
        "acumulado": 1 if data_api.get("acumulado") else 0,
        "valor_premio": valor_premio,
        "ganhadores": ganhadores,
        **{c: faixas_secundarias.get(c) for c in COLUNAS_FAIXAS_SECUNDARIAS},
    }
    for i, d in enumerate(dezenas, start=1):
        linha[f"d{i:02d}"] = d
    for i, d in enumerate(ordem, start=1):
        linha[f"s{i:02d}"] = d
    return linha


# ─── credenciais do Supabase (env var > .env > config.json) ──────────────────

def _ler_dotenv(path: str) -> dict:
    valores = {}
    if not os.path.exists(path):
        return valores
    with open(path, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            valores[chave.strip()] = valor.strip().strip('"').strip("'")
    return valores

def _ler_config_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def carregar_credenciais_supabase(pasta: str = ".") -> tuple[str | None, str | None]:
    """Lê SUPABASE_URL/SUPABASE_ANON_KEY na ordem: variáveis de ambiente,
    depois .env na pasta informada, depois config.json na mesma pasta."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    if url and key:
        return url, key

    dotenv = _ler_dotenv(os.path.join(pasta, ".env"))
    url = url or dotenv.get("SUPABASE_URL")
    key = key or dotenv.get("SUPABASE_ANON_KEY")
    if url and key:
        return url, key

    config = _ler_config_json(os.path.join(pasta, "config.json"))
    url = url or config.get("SUPABASE_URL")
    key = key or config.get("SUPABASE_ANON_KEY")
    return url, key

def carregar_credencial_service_key(pasta: str = ".") -> str | None:
    """Mesma ordem de busca, mas para a chave service_role — usada só para
    escrita (INSERT) nos scripts de atualização/migração, nunca no HTML."""
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if key:
        return key
    dotenv = _ler_dotenv(os.path.join(pasta, ".env"))
    if dotenv.get("SUPABASE_SERVICE_KEY"):
        return dotenv["SUPABASE_SERVICE_KEY"]
    config = _ler_config_json(os.path.join(pasta, "config.json"))
    return config.get("SUPABASE_SERVICE_KEY")


# ─── backend SQLite ────────────────────────────────────────────────────────

class _SQLiteBackend:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(SCHEMA_SQLITE)
        self._garantir_colunas_faixas_secundarias()
        self.conn.commit()

    def _garantir_colunas_faixas_secundarias(self):
        """valor_premio_14/13/12/11 e ganhadores_14/13/12/11 foram adicionadas
        depois do schema original — ALTER TABLE idempotente pra bancos já existentes."""
        colunas_atuais = {row[1] for row in self.conn.execute("PRAGMA table_info(sorteios)")}
        for coluna in COLUNAS_FAIXAS_SECUNDARIAS:
            if coluna not in colunas_atuais:
                tipo = "INTEGER" if coluna.startswith("ganhadores_") else "REAL"
                self.conn.execute(f"ALTER TABLE sorteios ADD COLUMN {coluna} {tipo}")

    def ultimo_concurso(self) -> int:
        row = self.conn.execute("SELECT MAX(concurso) FROM sorteios").fetchone()
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
                f"INSERT INTO sorteios ({colunas_sql}) VALUES ({placeholders}) "
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
            cur = self.conn.execute("SELECT * FROM sorteios ORDER BY concurso ASC")
            return [dict(row) for row in cur.fetchall()]
        finally:
            self.conn.row_factory = row_factory_original

    def concursos_existentes(self) -> list[int]:
        cur = self.conn.execute("SELECT concurso FROM sorteios")
        return [row[0] for row in cur.fetchall()]

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
        status, corpo = self._request("GET", "sorteios?select=concurso&order=concurso.desc&limit=1")
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
                "POST", "sorteios?on_conflict=concurso",
                headers={"Prefer": "resolution=merge-duplicates,return=representation"},
                body=lote,
            )
            if status >= 400:
                raise RuntimeError(f"Supabase respondeu {status} ao inserir lote: {corpo}")
            inseridos += len(corpo) if isinstance(corpo, list) else 0
        return inseridos

    def carregar_todos(self) -> list[dict]:
        """Pagina em blocos de 1000 — o PostgREST limita select=* sem range a
        1000 linhas por padrão (db-max-rows). A Lotofácil passou dos 1000
        sorteios depois da carga total do histórico (--init-all), então isso
        deixou de ser um limite seguro de ignorar (mesmo ajuste já feito em
        megasena_db.py)."""
        TAMANHO_PAGINA = 1000
        todos = []
        offset = 0
        while True:
            status, corpo = self._request(
                "GET", "sorteios?select=*&order=concurso.asc",
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

    def concursos_existentes(self) -> list[int]:
        """Só os números de concurso (sem o resto das colunas) — usado por
        --init-all pra saber quais concursos já existem e pular no download,
        em vez de rebaixar tudo e confiar só no INSERT ignore-duplicates.
        Também paginado, pela mesma razão de carregar_todos()."""
        TAMANHO_PAGINA = 1000
        todos = []
        offset = 0
        while True:
            status, corpo = self._request(
                "GET", "sorteios?select=concurso&order=concurso.asc",
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

    def concursos_existentes(self) -> list[int]:
        return self._backend.concursos_existentes()

    def total_sorteios(self) -> int:
        return len(self.carregar_todos())

    def exportar_csv(self, path: str) -> int:
        registros = self.carregar_todos()
        fieldnames = (
            ["concurso", "data", "acumulado", "valor_premio_1", "ganhadores_1"]
            + [f"d{i:02d}" for i in range(1, 16)]
            + [f"s{i:02d}" for i in range(1, 16)]
        )
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
                for i in range(1, 16):
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

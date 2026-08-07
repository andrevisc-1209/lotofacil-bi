# Loterias BI

Dashboards de análise estatística — **Lotofácil** e **Mega-Sena** — frequência,
atraso, blocos, sequências, histórico completo em árvore e mais. Cada um é
gerado como um único HTML autocontido (Chart.js via CDN, sem build step) e
publicado no mesmo repositório/Supabase, com uma página de menu (`index.html`)
ligando os dois.

Funciona com **dois bancos intercambiáveis** por loteria: SQLite local
(padrão, zero configuração) ou Supabase (Postgres na nuvem, tier gratuito)
para manter os dados sempre disponíveis e publicar os dashboards online via
GitHub Pages.

## Estrutura do site publicado

```
index.html          → menu (liga para os dois dashboards)
lotofacil.html       → dashboard Lotofácil
megasena.html        → dashboard Mega-Sena
```

## Arquitetura

```
lotofacil_atualizar.py  ──┬──> Supabase (tabela sorteios, fonte primária)
                          └──> SQLite local (lotofacil.db, backup)
                                     │
                       lotofacil_bi.py --db / --source supabase
                                     │
                                     ▼
                              lotofacil.html

megasena_atualizar.py  ──┬──> Supabase (tabela megasena_sorteios, mesmo projeto)
                          └──> SQLite local (megasena.db, backup)
                                     │
                       megasena_bi.py --db / --source supabase
                                     │
                                     ▼
                              megasena.html
```

As duas loterias usam o **mesmo projeto Supabase** (mesmas credenciais em
`.env`/secrets), só em tabelas separadas (`sorteios` e `megasena_sorteios`).
`megasena_db.py`/`megasena_atualizar.py`/`megasena_bi.py` seguem exatamente o
mesmo padrão dos equivalentes da Lotofácil — só trocam universo de números
(01-60 em vez de 01-25), quantidade de dezenas por sorteio (6 em vez de 15) e
blocos (6 de 10 em vez de 5 de 5). O dashboard da Mega-Sena não tem simulador
de aposta nem ranking de jogos pessoais — só análise estatística.

## Uso local (só SQLite, sem Supabase)

```bash
# Lotofácil
python lotofacil_atualizar.py --init 500     # primeira carga
python lotofacil_bi.py --db lotofacil.db --output lotofacil.html

# Mega-Sena
python megasena_atualizar.py --init-all      # carga total do histórico (~2700+ sorteios)
python megasena_bi.py --db megasena.db --output megasena.html
```

Isso já funciona sem nenhuma conta ou configuração extra.

## Uso com Supabase (banco na nuvem)

### 1. Criar as tabelas

No painel do Supabase (supabase.com → seu projeto → SQL Editor), rode o
conteúdo de [`supabase_schema.sql`](supabase_schema.sql) (Lotofácil) e de
[`megasena_schema.sql`](megasena_schema.sql) (Mega-Sena).

### 2. Configurar credenciais

Copie `.env.example` para `.env` e preencha com os valores de
**Settings → API** do seu projeto (as mesmas credenciais servem para as duas
loterias — só a tabela alvo muda):

```
SUPABASE_URL=https://SEU_PROJETO.supabase.co
SUPABASE_ANON_KEY=...       # "anon" "public" — pública, só leitura
SUPABASE_SERVICE_KEY=...    # "service_role" "secret" — privada, escrita
```

`.env` nunca é commitado (está no `.gitignore`). A `SUPABASE_ANON_KEY` é
segura para expor no HTML público porque a Row Level Security dos bancos só
permite `SELECT` sem autenticação — ninguém consegue escrever com ela. A
`SUPABASE_SERVICE_KEY` ignora RLS e por isso é usada **só** pelos scripts
locais e pelo GitHub Actions, nunca embutida no HTML.

### 3. Atualizar (Supabase + SQLite juntos)

```bash
# Lotofácil
python lotofacil_atualizar.py                # baixa só os concursos novos
python lotofacil_atualizar.py --only-local    # ignora o Supabase (offline)

# Mega-Sena
python megasena_atualizar.py                  # baixa só os concursos novos
python megasena_atualizar.py --init-all       # carga total (só na primeira vez)
python megasena_atualizar.py --only-local     # ignora o Supabase (offline)
```

Se `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` não estiverem configurados, ou o
Supabase estiver fora do ar, os scripts caem automaticamente para SQLite
local com um aviso — nunca travam o fluxo.

### 4. Gerar os dashboards a partir do Supabase

```bash
python lotofacil_bi.py --source supabase
python megasena_bi.py --source supabase
```

Cada HTML gerado ganha um indicador no cabeçalho ("🔄 Verificar novos
sorteios") que faz uma consulta leve ao Supabase direto no navegador para
avisar se já existe um concurso mais novo do que o embutido no HTML — sem
recalcular nada, só um aviso para você rodar o gerador de novo. O botão
"Atualizar dados" (token do GitHub salvo no navegador) dispara o workflow
certo pra cada loteria — o token é compartilhado entre os dois dashboards
(mesmo repositório, mesma origem).

## Publicar no GitHub Pages com atualização automática

1. Dê push do código (não esqueça: `lotofacil.db`, `megasena.db` e `.env`
   ficam de fora, o `.gitignore` já cuida disso).
2. Ative o GitHub Pages em **Settings → Pages** apontando para a branch
   `main`, pasta `/` (raiz) — `index.html` (o menu) precisa estar na raiz.
3. Configure os secrets em **Settings → Secrets and variables → Actions →
   New repository secret** (servem para as duas loterias):
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_KEY`
4. Rode a carga inicial manualmente pelo menos uma vez antes de habilitar os
   workflows: `python lotofacil_atualizar.py --init 500` e
   `python megasena_atualizar.py --init-all`.
5. O workflow [`.github/workflows/atualizar.yml`](.github/workflows/atualizar.yml)
   roda às segundas, quartas e sextas às 22h UTC (Lotofácil), e
   [`.github/workflows/megasena_atualizar.yml`](.github/workflows/megasena_atualizar.yml)
   roda às quartas e sábados às 23h UTC (Mega-Sena) — cada um atualiza o
   Supabase, regera o HTML correspondente e faz commit + push
   automaticamente. Também dá pra disparar manualmente em **Actions → (nome
   do workflow) → Run workflow**.

## Scripts

| Script | Função |
|---|---|
| **Lotofácil** | |
| `lotofacil_coletar.py` | Coleta inicial simples direto pra CSV (uso pontual) |
| `lotofacil_db.py` | Camada de dados — `Database.sqlite(...)` / `Database.supabase(...)` |
| `lotofacil_atualizar.py` | Atualização incremental (Supabase + SQLite, com fallback) |
| `lotofacil_migrar.py` | Migra o SQLite local para o Supabase |
| `lotofacil_bi.py` | Gera `lotofacil.html` (`--db`, `--source supabase`, `--periodo`) |
| `lotofacil_simular.py` | Backtesta jogos fixos contra o histórico |
| `lotofacil_gerar_jogos.py` | Gera jogos seguindo padrões estatísticos do histórico |
| **Mega-Sena** | |
| `megasena_db.py` | Camada de dados — mesmo padrão de `lotofacil_db.py`, tabela `megasena_sorteios` |
| `megasena_atualizar.py` | Atualização incremental (`--init-all` para carga total do histórico) |
| `megasena_bi.py` | Gera `megasena.html` (`--db`, `--source supabase`, `--periodo`) |

## Requisitos

Python 3.9+, stdlib apenas — nenhuma dependência externa (`urllib` no lugar
de `supabase-py`, `sqlite3` da própria stdlib).

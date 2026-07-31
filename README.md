# Lotofácil BI

Dashboard de análise estatística da Lotofácil — frequência, atraso, blocos,
sequências, histórico completo em árvore, simulador de jogos e mais. Gerado
como um único HTML autocontido (Chart.js via CDN, sem build step).

Funciona com **dois bancos intercambiáveis**: SQLite local (padrão, zero
configuração) ou Supabase (Postgres na nuvem, tier gratuito) para manter os
dados sempre disponíveis e publicar o dashboard online via GitHub Pages.

## Arquitetura

```
lotofacil_atualizar.py  ──┬──> Supabase (fonte primária, se configurado)
                          └──> SQLite local (sempre, como backup)
                                     │
lotofacil_bi.py --db / --source supabase
                                     │
                                     ▼
                          lotofacil_bi.html  →  GitHub Pages
```

## Uso local (só SQLite, sem Supabase)

```bash
python lotofacil_atualizar.py --init 500     # primeira carga
python lotofacil_bi.py --db lotofacil.db     # gera o dashboard
open lotofacil_bi.html
```

Isso já funciona sem nenhuma conta ou configuração extra.

## Uso com Supabase (banco na nuvem)

### 1. Criar a tabela

No painel do Supabase (supabase.com → seu projeto → SQL Editor), rode o
conteúdo de [`supabase_schema.sql`](supabase_schema.sql).

### 2. Configurar credenciais

Copie `.env.example` para `.env` e preencha com os valores de
**Settings → API** do seu projeto:

```
SUPABASE_URL=https://SEU_PROJETO.supabase.co
SUPABASE_ANON_KEY=...       # "anon" "public" — pública, só leitura
SUPABASE_SERVICE_KEY=...    # "service_role" "secret" — privada, escrita
```

`.env` nunca é commitado (está no `.gitignore`). A `SUPABASE_ANON_KEY` é
segura para expor no HTML público porque a Row Level Security do banco só
permite `SELECT` sem autenticação — ninguém consegue escrever com ela. A
`SUPABASE_SERVICE_KEY` ignora RLS e por isso é usada **só** pelos scripts
locais e pelo GitHub Actions, nunca embutida no HTML.

### 3. Migrar os dados existentes (se já tiver um `lotofacil.db` local)

```bash
python lotofacil_migrar.py --from sqlite --to supabase
```

### 4. Atualizar (Supabase + SQLite juntos)

```bash
python lotofacil_atualizar.py                # baixa só os concursos novos
python lotofacil_atualizar.py --only-local    # ignora o Supabase (offline)
python lotofacil_atualizar.py --source local  # descobre o delta pelo SQLite
```

Se `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` não estiverem configurados, ou o
Supabase estiver fora do ar, o script cai automaticamente para SQLite local
com um aviso — nunca trava o fluxo.

### 5. Gerar o dashboard a partir do Supabase

```bash
python lotofacil_bi.py --source supabase
```

O HTML gerado é idêntico ao modo offline (mesmo cálculo em Python, mesmo
arquivo autocontido) — só que os dados vieram do Supabase. Ele também ganha
um indicador no cabeçalho ("🔄 Verificar novos sorteios") que faz uma
consulta leve ao Supabase direto no navegador para avisar se já existe um
concurso mais novo do que o embutido no HTML — sem recalcular nada, só um
aviso para você rodar o gerador de novo.

> Uma versão que recalculasse *todo* o dashboard no navegador a cada consulta
> exigiria reescrever em JavaScript todas as ~30 análises que hoje rodam em
> Python (frequência, blocos, períodos, árvore de histórico, financeiro...).
> Não fizemos isso por ser um projeto do tamanho do dashboard inteiro de novo
> e arriscado de acertar sem bugs — o modelo atual (Python gera o HTML,
> GitHub Actions publica de novo a cada atualização) já entrega dados sempre
> atuais na prática, já que a Lotofácil só sorteia poucas vezes por semana.

## Publicar no GitHub Pages com atualização automática

1. Crie o repositório e dê push do código (não esqueça: `lotofacil.db` e
   `.env` ficam de fora, o `.gitignore` já cuida disso).
2. Ative o GitHub Pages em **Settings → Pages** apontando para a branch
   `main`, pasta `/` (raiz) — `lotofacil_bi.html` precisa estar na raiz do
   repositório.
3. Configure os secrets em **Settings → Secrets and variables → Actions →
   New repository secret**:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_KEY`
4. Rode a migração inicial (`lotofacil_migrar.py`) manualmente pelo menos uma
   vez antes de habilitar o workflow — se o Supabase estiver vazio quando o
   Actions rodar, ele importa os últimos 500 sorteios automaticamente, mas é
   melhor você controlar essa primeira carga.
5. O workflow [`.github/workflows/atualizar.yml`](.github/workflows/atualizar.yml)
   roda às segundas, quartas e sextas às 22h (UTC), atualiza o Supabase,
   regera `lotofacil_bi.html` e faz commit + push automaticamente. Também dá
   pra disparar manualmente em **Actions → Atualizar sorteios → Run workflow**.

## Scripts

| Script | Função |
|---|---|
| `lotofacil_coletar.py` | Coleta inicial simples direto pra CSV (uso pontual) |
| `lotofacil_db.py` | Camada de dados — `Database.sqlite(...)` / `Database.supabase(...)` |
| `lotofacil_atualizar.py` | Atualização incremental (Supabase + SQLite, com fallback) |
| `lotofacil_migrar.py` | Migra o SQLite local para o Supabase |
| `lotofacil_bi.py` | Gera o dashboard HTML (`--db`, `--source supabase`, `--periodo`) |
| `lotofacil_simular.py` | Backtesta jogos fixos contra o histórico |
| `lotofacil_gerar_jogos.py` | Gera jogos seguindo padrões estatísticos do histórico |

## Requisitos

Python 3.9+, stdlib apenas — nenhuma dependência externa (`urllib` no lugar
de `supabase-py`, `sqlite3` da própria stdlib).

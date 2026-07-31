-- ============================================================================
-- supabase_schema.sql
-- ----------------------------------------------------------------------------
-- Schema da tabela de sorteios da Lotofácil para o Supabase (Postgres gerenciado).
--
-- Como usar:
--   1. Acesse supabase.com -> seu projeto -> SQL Editor
--   2. Cole este arquivo inteiro e clique em "Run"
--   3. Guarde suas chaves em Settings -> API:
--        - "Project URL"           -> vai em SUPABASE_URL
--        - "anon" "public"         -> vai em SUPABASE_ANON_KEY (pública, só leitura)
--        - "service_role" "secret" -> vai em SUPABASE_SERVICE_KEY (secreta, só nos
--                                     scripts e no GitHub Actions — NUNCA no HTML)
-- ============================================================================

-- Tabela principal de sorteios
CREATE TABLE IF NOT EXISTS sorteios (
    concurso      INTEGER PRIMARY KEY,
    data          DATE NOT NULL,
    data_br       TEXT,
    acumulado     BOOLEAN DEFAULT FALSE,
    valor_premio  NUMERIC(15,2),
    ganhadores    INTEGER,
    d01 SMALLINT, d02 SMALLINT, d03 SMALLINT, d04 SMALLINT, d05 SMALLINT,
    d06 SMALLINT, d07 SMALLINT, d08 SMALLINT, d09 SMALLINT, d10 SMALLINT,
    d11 SMALLINT, d12 SMALLINT, d13 SMALLINT, d14 SMALLINT, d15 SMALLINT,
    s01 SMALLINT, s02 SMALLINT, s03 SMALLINT, s04 SMALLINT, s05 SMALLINT,
    s06 SMALLINT, s07 SMALLINT, s08 SMALLINT, s09 SMALLINT, s10 SMALLINT,
    s11 SMALLINT, s12 SMALLINT, s13 SMALLINT, s14 SMALLINT, s15 SMALLINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Índice para queries por data
CREATE INDEX IF NOT EXISTS idx_sorteios_data ON sorteios(data DESC);

-- Row Level Security
ALTER TABLE sorteios ENABLE ROW LEVEL SECURITY;

-- Leitura pública: necessário para o dashboard HTML consultar sem autenticação.
-- Segura porque é só SELECT — ninguém consegue alterar dados com a anon key.
DROP POLICY IF EXISTS "leitura publica" ON sorteios;
CREATE POLICY "leitura publica" ON sorteios FOR SELECT USING (true);

-- IMPORTANTE — sobre escrita (INSERT/UPDATE):
-- Esta tabela NÃO tem política de escrita para a role "anon" de propósito.
-- Isso significa que a SUPABASE_ANON_KEY (a mesma chave pública embutida no
-- HTML) NÃO consegue inserir ou alterar linhas — só ler. Isso é intencional:
-- se qualquer visitante do dashboard pudesse ver a chave E usá-la para
-- escrever, qualquer um poderia poluir sua base de sorteios.
--
-- lotofacil_atualizar.py / lotofacil_migrar.py escrevem usando a chave
-- "service_role" (SUPABASE_SERVICE_KEY), que ignora RLS por definição do
-- Supabase — não precisa (e não deve) criar uma policy de INSERT aqui.
-- Guarde a service_role key só localmente (.env, gitignored) e como secret
-- do GitHub Actions — nunca no HTML nem em qualquer arquivo commitado.

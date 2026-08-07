-- megasena_schema.sql
-- ---------------------------------------------------------------------------
-- Tabela da Mega-Sena no mesmo projeto Supabase da Lotofácil. Roda no SQL
-- Editor do Supabase. Segue o mesmo padrão de supabase_schema.sql (Lotofácil):
-- leitura pública via RLS, escrita só com a service_role key (usada apenas
-- por megasena_atualizar.py, nunca embutida no HTML).

CREATE TABLE IF NOT EXISTS megasena_sorteios (
    concurso      INTEGER PRIMARY KEY,
    data          DATE NOT NULL,
    data_br       TEXT,
    acumulado     BOOLEAN DEFAULT FALSE,
    valor_premio  NUMERIC(15,2),
    ganhadores    INTEGER,
    d01 SMALLINT, d02 SMALLINT, d03 SMALLINT,
    d04 SMALLINT, d05 SMALLINT, d06 SMALLINT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_megasena_data ON megasena_sorteios(data DESC);

ALTER TABLE megasena_sorteios ENABLE ROW LEVEL SECURITY;

-- leitura pública (usada pelo dashboard estático via SUPABASE_ANON_KEY)
CREATE POLICY "leitura publica megasena" ON megasena_sorteios FOR SELECT USING (true);

-- de propósito SEM política de INSERT/UPDATE aqui: escrita só acontece via
-- megasena_atualizar.py, autenticado com a SUPABASE_SERVICE_KEY (que ignora
-- RLS), nunca com a anon key exposta no HTML. Mesmo racional do
-- supabase_schema.sql da Lotofácil.

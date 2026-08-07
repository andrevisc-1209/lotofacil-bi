-- lotomania_schema.sql
-- ----------------------
-- Tabela da Lotomania no Supabase — mesmo padrão de supabase_schema.sql
-- (Lotofácil) e megasena_schema.sql (Mega-Sena), só trocando o universo de
-- números (01-100) e a quantidade de dezenas sorteadas (20 em vez de 15/6).
--
-- Rodar no SQL Editor do Supabase antes de usar lotomania_db.py.

CREATE TABLE IF NOT EXISTS lotomania_sorteios (
    concurso      INTEGER PRIMARY KEY,
    data          DATE NOT NULL,
    data_br       TEXT,
    acumulado     BOOLEAN DEFAULT FALSE,
    valor_premio  NUMERIC(15,2),  -- faixa 1 (20 acertos)
    ganhadores    INTEGER,        -- faixa 1 (20 acertos)
    -- faixa 7 (0 acertos) — a faixa "surpresa", característica única da
    -- Lotomania (confirmado via API real: listaRateioPremio sempre tem uma
    -- entrada faixa=7 descricaoFaixa="0 acertos"). Adicionada além do
    -- schema originalmente pedido porque o card "Faixa Surpresa" do
    -- dashboard depende de saber quantos ganhadores/quanto pagou por
    -- sorteio, não só se o sorteio "foi surpresa" ou não.
    valor_surpresa     NUMERIC(15,2),
    ganhadores_surpresa INTEGER,
    -- 20 dezenas sorteadas (ordenadas)
    d01 SMALLINT, d02 SMALLINT, d03 SMALLINT, d04 SMALLINT, d05 SMALLINT,
    d06 SMALLINT, d07 SMALLINT, d08 SMALLINT, d09 SMALLINT, d10 SMALLINT,
    d11 SMALLINT, d12 SMALLINT, d13 SMALLINT, d14 SMALLINT, d15 SMALLINT,
    d16 SMALLINT, d17 SMALLINT, d18 SMALLINT, d19 SMALLINT, d20 SMALLINT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lotomania_data ON lotomania_sorteios(data DESC);

ALTER TABLE lotomania_sorteios ENABLE ROW LEVEL SECURITY;

CREATE POLICY "leitura publica lotomania"
    ON lotomania_sorteios FOR SELECT USING (true);

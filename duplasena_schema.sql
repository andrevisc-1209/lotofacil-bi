-- duplasena_schema.sql
-- ----------------------
-- Tabela da Dupla Sena no Supabase — mesmo padrão de megasena_schema.sql,
-- com o diferencial de guardar DOIS sorteios por concurso (d01-d06 = 1º
-- sorteio, s01-s06 = 2º sorteio) e 8 faixas de prêmio (3/4/5/6 acertos ×
-- 1ª/2ª rodada). Todas as faixas são variáveis (rateio por concurso).
--
-- Rodar no SQL Editor do Supabase antes de rodar os scripts.

CREATE TABLE IF NOT EXISTS duplasena_sorteios (
    concurso      INTEGER PRIMARY KEY,
    data          DATE NOT NULL,
    data_br       TEXT,
    acumulado     BOOLEAN DEFAULT FALSE,
    -- 1º sorteio (6 dezenas ordenadas)
    d01 SMALLINT, d02 SMALLINT, d03 SMALLINT,
    d04 SMALLINT, d05 SMALLINT, d06 SMALLINT,
    -- 2º sorteio (6 dezenas ordenadas)
    s01 SMALLINT, s02 SMALLINT, s03 SMALLINT,
    s04 SMALLINT, s05 SMALLINT, s06 SMALLINT,
    -- Prêmios por faixa — 1ª rodada (faixas 1-4 da API)
    valor_sena1      NUMERIC(15,2),
    ganhadores_sena1 INTEGER,
    valor_quina1     NUMERIC(15,2),
    ganhadores_quina1 INTEGER,
    valor_quadra1    NUMERIC(15,2),
    ganhadores_quadra1 INTEGER,
    valor_terno1     NUMERIC(15,2),
    ganhadores_terno1  INTEGER,
    -- Prêmios por faixa — 2ª rodada (faixas 5-8 da API)
    valor_sena2      NUMERIC(15,2),
    ganhadores_sena2 INTEGER,
    valor_quina2     NUMERIC(15,2),
    ganhadores_quina2 INTEGER,
    valor_quadra2    NUMERIC(15,2),
    ganhadores_quadra2 INTEGER,
    valor_terno2     NUMERIC(15,2),
    ganhadores_terno2  INTEGER,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_duplasena_data ON duplasena_sorteios(data DESC);

ALTER TABLE duplasena_sorteios ENABLE ROW LEVEL SECURITY;

CREATE POLICY "leitura publica duplasena"
    ON duplasena_sorteios FOR SELECT USING (true);

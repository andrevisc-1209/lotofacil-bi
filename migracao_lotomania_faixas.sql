-- Lotomania: faixas 2-6 (19/18/17/16/15 acertos) — todas variáveis (rateio
-- por sorteio), mesmo padrão já usado para a faixa 7 (surpresa).
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS valor_dezenove NUMERIC(15,2);
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS ganhadores_dezenove INTEGER;
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS valor_dezoito NUMERIC(15,2);
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS ganhadores_dezoito INTEGER;
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS valor_dezessete NUMERIC(15,2);
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS ganhadores_dezessete INTEGER;
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS valor_dezesseis NUMERIC(15,2);
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS ganhadores_dezesseis INTEGER;
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS valor_quinze NUMERIC(15,2);
ALTER TABLE lotomania_sorteios ADD COLUMN IF NOT EXISTS ganhadores_quinze INTEGER;

-- ============================================================================
-- migracao_faixas_reais.sql
-- ----------------------------------------------------------------------------
-- Adiciona colunas para os valores REAIS por sorteio das faixas de prêmio
-- secundárias — até agora só a faixa principal (15 acertos da Lotofácil,
-- sena da Mega-Sena) tinha valor_premio real; as demais faixas usavam uma
-- referência fixa no código. Confirmado via API real da Caixa que os valores
-- de 13/12/11 acertos da Lotofácil também mudaram ao longo da história
-- (ex.: concurso 1500 = R$20/8/4, concurso 3000 = R$30/12/6, concurso 3500+
-- = R$35/14/7) — não são realmente fixos, então também passam a ser
-- capturados por sorteio em vez de usar uma única constante.
--
-- Rodar no SQL Editor do Supabase antes do reprocessamento do histórico.
-- ============================================================================

-- Lotofácil: faixas 2 (14 acertos), 3 (13), 4 (12), 5 (11)
ALTER TABLE sorteios ADD COLUMN IF NOT EXISTS valor_premio_14 NUMERIC(15,2);
ALTER TABLE sorteios ADD COLUMN IF NOT EXISTS ganhadores_14 INTEGER;
ALTER TABLE sorteios ADD COLUMN IF NOT EXISTS valor_premio_13 NUMERIC(15,2);
ALTER TABLE sorteios ADD COLUMN IF NOT EXISTS ganhadores_13 INTEGER;
ALTER TABLE sorteios ADD COLUMN IF NOT EXISTS valor_premio_12 NUMERIC(15,2);
ALTER TABLE sorteios ADD COLUMN IF NOT EXISTS ganhadores_12 INTEGER;
ALTER TABLE sorteios ADD COLUMN IF NOT EXISTS valor_premio_11 NUMERIC(15,2);
ALTER TABLE sorteios ADD COLUMN IF NOT EXISTS ganhadores_11 INTEGER;

-- Mega-Sena: faixas 2 (quina, 5 acertos), 3 (quadra, 4 acertos)
ALTER TABLE megasena_sorteios ADD COLUMN IF NOT EXISTS valor_quina NUMERIC(15,2);
ALTER TABLE megasena_sorteios ADD COLUMN IF NOT EXISTS ganhadores_quina INTEGER;
ALTER TABLE megasena_sorteios ADD COLUMN IF NOT EXISTS valor_quadra NUMERIC(15,2);
ALTER TABLE megasena_sorteios ADD COLUMN IF NOT EXISTS ganhadores_quadra INTEGER;

"""
lotomania_bi.py
----------------
Gera o dashboard HTML interativo da Lotomania — mesmo padrão visual e
arquitetural de megasena_bi.py/lotofacil_bi.py (Python pré-computa tudo por
período, JS só renderiza), incluindo "Meus Jogos" (validador financeiro) e
Simulador de aposta — com o esquema de pontuação INVERTIDO da Lotomania (ver
calc_jogos_lotomania) e a faixa 7 "surpresa" (0 acertos) como vitória real.

Universo real: 00-99 (100 números, zero incluído) — confirmado via API real
da Caixa (o concurso 1 já tinha "00" em listaDezenas). 20 dezenas sorteadas
por concurso; aposta mínima de 50 dezenas fixas (não configurável — não afeta
nenhuma feature deste dashboard), R$3,00 por aposta. Sorteios aos sábados;
concurso 1 em 02/10/1999.

A faixa 7 ("surpresa", 0 acertos) é exclusiva da Lotomania no cenário das
loterias da Caixa — ganha quem NÃO acerta nenhuma das 20 dezenas sorteadas.
Por ser a característica mais distintiva dessa loteria, ganha card dedicado
na Aba 1 (calc_faixa_surpresa) em vez de aparecer só como rodapé financeiro.

Uso:
    python lotomania_bi.py --db lotomania.db --source local
    python lotomania_bi.py --source supabase
    python lotomania_bi.py --output lotomania.html
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from math import comb, exp
from pathlib import Path

# placeholder — números aleatórios, o usuário substitui depois (não são
# "sugeridos" nem seguem nenhum padrão estatístico, ao contrário da Lotofácil)
JOGOS_LOTOMANIA = {
    "Jogo 01": [2,3,5,8,9,10,12,14,15,20,21,24,26,29,31,34,35,37,40,43,
                44,45,46,47,48,49,53,54,58,59,65,66,68,70,73,74,76,77,78,79,
                80,83,88,91,92,93,94,97,98,99],
    "Jogo 02": [0,6,7,8,11,14,16,17,18,19,20,24,27,28,29,31,33,34,35,38,
                40,43,46,50,51,53,54,55,58,62,63,65,68,71,72,74,76,77,78,79,
                82,83,84,89,90,91,95,96,98,99],
    "Jogo 03": [0,2,4,5,7,8,13,14,19,20,22,25,30,31,33,34,36,37,38,39,
                41,43,46,47,48,49,52,55,56,58,59,60,62,64,65,67,68,69,73,77,
                80,81,82,85,87,89,91,94,96,97],
    "Jogo 04": [0,2,4,7,8,9,13,15,16,17,21,25,27,28,29,30,31,32,33,34,
                36,39,42,43,46,47,51,53,54,56,57,60,61,64,66,67,69,70,72,76,
                77,78,79,81,83,84,88,89,92,96],
    "Jogo 05": [1,3,6,7,9,10,11,12,13,15,17,23,24,25,26,30,31,35,43,45,
                48,51,52,54,55,56,57,58,59,60,68,69,70,73,77,78,79,80,82,83,
                84,86,88,89,91,94,95,96,97,98],
    "Jogo 06": [0,5,6,7,8,10,13,15,16,19,20,23,24,26,27,30,31,33,36,37,
                39,40,42,45,49,51,54,55,58,59,61,62,63,64,65,67,69,71,74,78,
                80,82,83,84,85,87,89,91,95,97],
    "Jogo 07": [1,6,8,9,12,13,14,16,17,18,20,21,27,30,31,33,35,36,38,40,
                43,44,45,47,50,54,56,58,64,65,66,67,68,69,70,71,72,75,79,80,
                82,84,85,86,89,90,92,93,96,98],
    "Jogo 08": [0,1,4,5,6,9,11,13,14,15,16,18,19,20,22,26,32,33,35,39,
                42,43,45,46,47,49,54,55,56,60,62,64,66,67,69,70,71,72,76,81,
                85,86,87,89,90,91,92,94,98,99],
    "Jogo 09": [2,3,4,6,8,11,13,14,16,20,22,24,25,27,28,29,31,33,34,35,
                37,38,39,42,44,46,48,50,51,52,56,57,58,60,65,72,73,74,76,80,
                83,85,88,93,94,95,96,97,98,99],
    "Jogo 10": [0,5,8,13,14,15,16,18,19,20,22,24,25,27,29,32,35,37,38,39,
                40,41,42,46,48,49,50,51,52,53,55,64,65,66,67,68,70,71,72,73,
                76,77,79,85,87,90,91,92,95,96],
}
CUSTO_LOTOMANIA = 3.00


# ─── carrega dados do banco (SQLite ou Supabase, via lotomania_db.Database) ───

def carregar_de_database(db) -> list[dict]:
    """Mesma lógica de lotofacil_bi.carregar_de_database: linhas com 'data' em
    formato BR (igual ao que a UI espera) e 'data_iso' extra pro bucketing temporal."""
    registros = db.carregar_todos()
    rows = []
    for r in registros:
        linha = {k: v for k, v in r.items() if k not in ("data", "data_br")}
        linha["concurso"] = str(r["concurso"])
        linha["data_iso"] = r["data"]
        linha["data"] = r.get("data_br") or r["data"]
        rows.append(linha)
    return rows

def data_iso_de(row: dict) -> str:
    if row.get("data_iso"):
        return row["data_iso"]
    dia, mes, ano = row["data"].split("/")
    return f"{ano}-{mes}-{dia}"

def filtrar_por_periodo(rows: list[dict], periodo: str) -> list[dict]:
    return [r for r in rows if data_iso_de(r).startswith(periodo)]

def dezenas(row: dict) -> list[int]:
    return sorted(int(row[f"d{i:02d}"]) for i in range(1, 21))


# ─── análises (universo 00-99, 20 dezenas por sorteio) ────────────────────────

def calc_frequencia(sorteios):
    c = Counter()
    for s in sorteios:
        c.update(s)
    return c

def calc_atraso(sorteios):
    n = len(sorteios)
    ultimo = {}
    for i, s in enumerate(sorteios):
        for d in s:
            ultimo[d] = i
    return {d: n - 1 - ultimo.get(d, -1) for d in range(0, 100)}

def calc_pares_impares(sorteios):
    resultados = []
    for s in sorteios:
        p = sum(1 for d in s if d % 2 == 0)  # 0 é par — comportamento padrão do Python, correto aqui
        resultados.append({"pares": p, "impares": 20 - p})
    return resultados

def calc_faixas(sorteios):
    """Distribuição por sorteio usando os MESMOS 5 blocos de 20 da aba
    Blocos (01-20/21-40/41-60/61-80/81-00) — diferente da Mega-Sena/Lotofácil,
    que usam terços do universo: com 100 números não faz sentido inventar um
    corte diferente do que já é mostrado na aba dedicada. O bloco E "fecha o
    ciclo": 81-99 mais o 00 (equivalente ao 100, que não existe nesse
    universo zero-indexado)."""
    resultados = []
    for s in sorteios:
        resultados.append({
            "A": sum(1 for d in s if 1 <= d <= 20),
            "B": sum(1 for d in s if 21 <= d <= 40),
            "C": sum(1 for d in s if 41 <= d <= 60),
            "D": sum(1 for d in s if 61 <= d <= 80),
            "E": sum(1 for d in s if d == 0 or 81 <= d <= 99),
        })
    return resultados

def calc_soma(sorteios):
    return [sum(s) for s in sorteios]

def calc_coocorrencia(sorteios, top_n=20):
    c = Counter()
    for s in sorteios:
        for a, b in combinations(sorted(s), 2):
            c[(a, b)] += 1
    return c.most_common(top_n)

def calc_coocorrencia_completa(sorteios):
    c = Counter()
    for s in sorteios:
        for a, b in combinations(sorted(s), 2):
            c[(a, b)] += 1
    return c

def calc_anticorrelacao(cooc_completo, bottom_n=15):
    """Pares com menor co-ocorrência (só entre os que saíram juntos ao menos
    1 vez — pares com zero aparições já ficam fora do Counter)."""
    itens = sorted(cooc_completo.items(), key=lambda kv: kv[1])
    return itens[:bottom_n]


# ─── detector de sorteios com dezenas idênticas (curiosidade estatística) ────
# C(100,20) ≈ 5,36 × 10^20 combinações possíveis — astronomicamente maior que
# C(60,6) da Mega-Sena ou C(25,15) da Lotofácil, então uma repetição aqui seria
# ainda mais improvável estatisticamente que nas outras duas loterias.

TOTAL_COMBINACOES = comb(100, 20)

def detectar_repeticoes_lotomania(rows: list[dict]) -> list[dict]:
    """Agrupa sorteios pelas 20 dezenas (ordenadas) e retorna só os grupos com
    mais de 1 concurso — mesma lógica de detectar_repeticoes da Mega-Sena/
    Lotofácil, calculada sobre o histórico completo (não depende do filtro de
    período)."""
    grupos = defaultdict(list)
    for r in rows:
        chave = tuple(sorted(int(r[f"d{i:02d}"]) for i in range(1, 21)))
        grupos[chave].append({
            "concurso": _to_int(r["concurso"]),
            "data": r.get("data_br") or r["data"],
        })

    repeticoes = []
    for numeros, sorteios in grupos.items():
        if len(sorteios) > 1:
            repeticoes.append({
                "numeros": list(numeros),
                "vezes": len(sorteios),
                "sorteios": sorted(sorteios, key=lambda x: x["concurso"]),
            })
    return sorted(repeticoes, key=lambda x: x["vezes"], reverse=True)

def prob_repeticao(n_sorteios: int, n_combinacoes: int) -> float:
    """Probabilidade aproximada (problema do aniversário) de pelo menos 1
    repetição depois de n_sorteios sorteios."""
    if n_sorteios < 2:
        return 0.0
    prob = 1 - exp(-n_sorteios * (n_sorteios - 1) / (2 * n_combinacoes))
    return round(prob * 100, 4)

def calc_sequencias_consecutivas(sorteios):
    """Runs de números consecutivos dentro de cada sorteio de 20 dezenas. As
    abas do dashboard só mostram tamanho 2, 3 e 4 (mesmo recorte da
    Mega-Sena), mas dist_tamanho guarda qualquer tamanho encontrado (usado
    pelo KPI 'maior sequência vista') — com 20 números de 100, sequências
    bem mais longas que isso aparecem no histórico."""
    todas_runs = []
    dist_tamanho = Counter()

    for s in sorteios:
        ordenado = sorted(s)
        run_atual = [ordenado[0]]
        for d in ordenado[1:]:
            if d == run_atual[-1] + 1:
                run_atual.append(d)
            else:
                if len(run_atual) >= 2:
                    todas_runs.append(tuple(run_atual))
                    dist_tamanho[len(run_atual)] += 1
                run_atual = [d]
        if len(run_atual) >= 2:
            todas_runs.append(tuple(run_atual))
            dist_tamanho[len(run_atual)] += 1

    top_por_tamanho = {}
    for tam in range(2, 5):  # abas: 2, 3, 4
        runs_tam = [r for r in todas_runs if len(r) == tam]
        c = Counter(runs_tam)
        top_por_tamanho[tam] = [{"seq": list(r), "count": cnt} for r, cnt in c.most_common(10)]

    return dist_tamanho, top_por_tamanho

def calc_tendencia(sorteios, janela=50):
    """Frequência nos últimos `janela` sorteios vs. geral. Cada sorteio da
    Lotomania cobre 20/100=20% do universo — mais denso que a Mega-Sena
    (6/60=10%, janela 30), porém bem mais esparso que a Lotofácil (15/25=60%,
    janela 50). Como 100 números individuais precisam de mais amostras que 60
    pra um sinal de "recente" estabilizar, uma janela de 50 (maior que a da
    Mega-Sena, igual à da Lotofácil) equilibra sensibilidade com estabilidade."""
    n = len(sorteios)
    freq_total = calc_frequencia(sorteios)
    freq_rec = calc_frequencia(sorteios[-janela:])
    dados = []
    for d in range(0, 100):
        ft = round(freq_total.get(d, 0) / n * 100, 1) if n else 0
        fr = round(freq_rec.get(d, 0) / min(janela, n) * 100, 1) if n else 0
        dados.append({"d": d, "total": ft, "recente": fr, "delta": round(fr - ft, 1)})
    return dados

def calc_ciclo_medio(sorteios):
    indices = defaultdict(list)
    for i, s in enumerate(sorteios):
        for d in s:
            indices[d].append(i)
    resultado = {}
    for d in range(0, 100):
        idx = indices.get(d, [])
        intervalos = [b - a for a, b in zip(idx, idx[1:])]
        resultado[d] = {
            "ciclo": round(sum(intervalos) / len(intervalos), 1) if intervalos else None,
            "aparicoes": len(idx),
        }
    return resultado

def calc_repeticao_anterior(sorteios):
    return [len(set(sorteios[i]) & set(sorteios[i - 1])) for i in range(1, len(sorteios))]


# ─── blocos de 20 (A: 01-20, B: 21-40, C: 41-60, D: 61-80, E: 81-00) ──────────
# Bloco E fecha o ciclo com o 00 (equivalente ao "100" que não existe nesse
# universo zero-indexado) em vez do 0 abrir um bloco à parte — mantém a
# mesma cara "01-20/21-40/..." de uma loteria 1-indexada, só trocando 100→00.

BLOCOS_NOMES = ["A", "B", "C", "D", "E"]
BLOCOS_DEZENAS = [
    list(range(1, 21)), list(range(21, 41)), list(range(41, 61)),
    list(range(61, 81)), list(range(81, 100)) + [0],
]

def bloco_de(d: int) -> int:
    return 4 if d == 0 else (d - 1) // 20

def _contagem_blocos(s) -> list:
    c = Counter(bloco_de(d) for d in s)
    return [c.get(i, 0) for i in range(5)]

def calc_blocos_freq_individual(sorteios):
    freq = calc_frequencia(sorteios)
    resultado = {}
    for nome, dezenas_bloco in zip(BLOCOS_NOMES, BLOCOS_DEZENAS):
        resultado[nome] = {d: freq.get(d, 0) for d in dezenas_bloco}
    return resultado

def calc_blocos_combinacoes(sorteios, top_n=15):
    """Assinaturas de distribuição mais frequentes, ex: '4-4-4-4-4' → sorteio
    perfeitamente distribuído entre os 5 blocos (20 dezenas / 5 blocos = 4.0
    de média — é a combinação mais provável, não uma coincidência rara)."""
    n = len(sorteios)
    contador = Counter()
    for s in sorteios:
        c = _contagem_blocos(s)
        contador["-".join(str(v) for v in c)] += 1
    return [
        {"combinacao": combo, "count": cnt, "pct": round(cnt / n * 100, 1) if n else 0}
        for combo, cnt in contador.most_common(top_n)
    ]

def calc_blocos_coocorrencia(sorteios):
    """Matriz 5×5: frequência com que cada par de blocos contribui com >=4
    dezenas no mesmo sorteio (>=4 = pelo menos a média esperada por bloco,
    já que 20 dezenas / 5 blocos = 4.0)."""
    matriz = [[0] * 5 for _ in range(5)]
    for s in sorteios:
        c = _contagem_blocos(s)
        ativos = [i for i in range(5) if c[i] >= 4]
        for i in ativos:
            matriz[i][i] += 1
            for j in ativos:
                if i != j:
                    matriz[i][j] += 1
    return matriz

def calc_blocos_bundle(rows, sorteios):
    return {
        "freq_individual": calc_blocos_freq_individual(sorteios),
        "combinacoes": calc_blocos_combinacoes(sorteios, top_n=15),
        "coocorrencia": calc_blocos_coocorrencia(sorteios),
    }

def calc_blocos_periodo(rows, sorteios):
    """Média de dezenas por bloco, agrupado por mês (YYYY-MM) — histórico
    completo, não filtra por período do seletor (mesmo padrão da Mega-Sena/
    Lotofácil: o propósito é mostrar tendência AO LONGO do tempo)."""
    buckets = defaultdict(list)
    for row, s in zip(rows, sorteios):
        periodo = data_iso_de(row)[:7]
        buckets[periodo].append(_contagem_blocos(s))
    resultado = []
    for periodo in sorted(buckets):
        valores = buckets[periodo]
        medias = [round(sum(v[i] for v in valores) / len(valores), 2) for i in range(5)]
        resultado.append({"periodo": periodo, "medias": medias, "total": len(valores)})
    return resultado


# ─── resumo financeiro (faixa 1 = 20 acertos, prêmio maior) ──────────────────

def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def _acumulado_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "sim")
    return False

def _meta_sorteio(row: dict) -> dict:
    """Metadados de um sorteio usados pela mesclagem client-side de múltiplos
    anos: além de concurso/data, inclui valor_premio/ganhadores/acumulado
    (faixa 1) e valor_surpresa/ganhadores_surpresa (faixa 7) — sem isso o JS
    não consegue recalcular nem o card financeiro nem o card Faixa Surpresa
    sobre um período mesclado (ver calcFinanceiroJS/calcFaixaSurpresaJS).
    valor_dezenove/dezoito/dezessete/dezesseis/quinze: valores reais das
    faixas 2-6 (19/18/17/16/15 acertos) — sem isso o JS não consegue
    recalcular "Meus Jogos"/o simulador sobre um período mesclado (ver
    calcJogosLotomaniaJS)."""
    return {
        "concurso": _to_int(row["concurso"]),
        "data": row["data"],
        "valor_premio": _to_float(row.get("valor_premio")),
        "ganhadores": _to_int(row.get("ganhadores")) or 0,
        "acumulado": _acumulado_bool(row.get("acumulado")),
        "valor_surpresa": _to_float(row.get("valor_surpresa")),
        "ganhadores_surpresa": _to_int(row.get("ganhadores_surpresa")) or 0,
        "valor_dezenove": _to_float(row.get("valor_dezenove")),
        "valor_dezoito": _to_float(row.get("valor_dezoito")),
        "valor_dezessete": _to_float(row.get("valor_dezessete")),
        "valor_dezesseis": _to_float(row.get("valor_dezesseis")),
        "valor_quinze": _to_float(row.get("valor_quinze")),
    }

def calc_financeiro(rows_p: list[dict]) -> dict:
    registros = []
    for row in rows_p:
        valor = _to_float(row.get("valor_premio"))
        ganhadores = _to_int(row.get("ganhadores"))
        acumulado = _acumulado_bool(row.get("acumulado"))
        if valor is not None:
            registros.append({
                "concurso": _to_int(row["concurso"]),
                "data": row["data"],
                "valor": valor,
                "ganhadores": ganhadores or 0,
                "acumulado": acumulado,
            })

    total_premios_pagos = sum(r["valor"] * r["ganhadores"] for r in registros)
    media_premio_faixa1 = round(sum(r["valor"] for r in registros) / len(registros), 2) if registros else None
    maior = max(registros, key=lambda r: r["valor"], default=None)
    menor = min(registros, key=lambda r: r["valor"], default=None)
    total_acumulados = sum(1 for r in registros if r["acumulado"])

    def _resumo_premio(r):
        return None if r is None else {"valor": r["valor"], "concurso": r["concurso"], "data": r["data"]}

    return {
        "total_premios_pagos": round(total_premios_pagos, 2),
        "media_premio_faixa1": media_premio_faixa1,
        "maior_premio": _resumo_premio(maior),
        "menor_premio": _resumo_premio(menor),
        "total_acumulados": total_acumulados,
        "total_sorteios": len(rows_p),
        "pct_acumulados": round(total_acumulados / len(rows_p) * 100, 1) if rows_p else 0,
    }


def calc_faixa_surpresa(rows_p: list[dict]) -> dict:
    """Faixa 7 da Lotomania: acertar ZERO das 20 dezenas sorteadas também
    premia — feature exclusiva dessa loteria entre as da Caixa. 'Sorteio com
    surpresa' = ganhadores_surpresa > 0 nesse concurso. Também calcula o
    maior intervalo (em sorteios) sem nenhuma ocorrência de surpresa,
    contando o gap ainda aberto até o fim do período (não só entre
    ocorrências)."""
    eventos = []
    maior_gap = 0
    gap_atual = 0
    for row in rows_p:
        ganhadores_surpresa = _to_int(row.get("ganhadores_surpresa")) or 0
        if ganhadores_surpresa > 0:
            eventos.append({
                "concurso": _to_int(row["concurso"]),
                "data": row.get("data_br") or row["data"],
                "ganhadores": ganhadores_surpresa,
                "valor": _to_float(row.get("valor_surpresa")),
            })
            maior_gap = max(maior_gap, gap_atual)
            gap_atual = 0
        else:
            gap_atual += 1
    maior_gap = max(maior_gap, gap_atual)

    n = len(rows_p)
    qtd = len(eventos)
    media_ganhadores = round(sum(e["ganhadores"] for e in eventos) / qtd, 1) if qtd else None
    mais_recente = eventos[-1] if eventos else None

    return {
        "total_sorteios": n,
        "qtd_sorteios_surpresa": qtd,
        "pct_sorteios_surpresa": round(qtd / n * 100, 1) if n else 0,
        "media_ganhadores_surpresa": media_ganhadores,
        "mais_recente": mais_recente,
        "maior_gap_sem_surpresa": maior_gap,
    }


# ─── "Meus Jogos" — validador financeiro (esquema INVERTIDO de pontuação) ────
# Lotomania: o jogador aposta 50 dezenas (de um universo de 100, 00-99); são
# sorteadas 20; acertos = quantas das 20 SORTEADAS caem dentro das 50
# apostadas — o oposto de Lotofácil/Mega-Sena, onde o jogador aposta poucas
# dezenas e o acerto é quantas delas saem no sorteio. 7 faixas premiam: 15 a
# 20 acertos E, exclusividade desta loteria, 0 acertos ("surpresa"). Todas as
# 7 usam o valor real por sorteio (sem fallback fixo — confirmado via API
# real que nenhuma faixa da Lotomania tem valor fixo ao longo da história).

FAIXA_LOTOMANIA = {
    20: {"nome": "vinte", "campo_valor": "valor_premio", "campo_ganhadores": "ganhadores"},
    19: {"nome": "dezenove", "campo_valor": "valor_dezenove", "campo_ganhadores": "ganhadores_dezenove"},
    18: {"nome": "dezoito", "campo_valor": "valor_dezoito", "campo_ganhadores": "ganhadores_dezoito"},
    17: {"nome": "dezessete", "campo_valor": "valor_dezessete", "campo_ganhadores": "ganhadores_dezessete"},
    16: {"nome": "dezesseis", "campo_valor": "valor_dezesseis", "campo_ganhadores": "ganhadores_dezesseis"},
    15: {"nome": "quinze", "campo_valor": "valor_quinze", "campo_ganhadores": "ganhadores_quinze"},
    0: {"nome": "surpresa", "campo_valor": "valor_surpresa", "campo_ganhadores": "ganhadores_surpresa"},
}
# prioridade pra decidir o "melhor resultado" de um jogo: 0 acertos é uma
# vitória genuína (faixa surpresa), mas como valor de prêmio ela costuma ser
# a menor das 7 — por isso entra em último na prioridade, não por "acertos"
# numérico puro (que faria 0 nunca vencer nenhuma comparação).
PRIORIDADE_FAIXA_LOTOMANIA = {20: 7, 19: 6, 18: 5, 17: 4, 16: 3, 15: 2, 0: 1}

def calc_jogos_lotomania(jogos_dict, rows_p, sorteios_p):
    """Validador financeiro completo de um dicionário {nome: [50 dezenas]}
    sobre um período: contagem por faixa (0, 15-20), histórico de sorteios em
    que pontuou (lista expandível + mini gráfico), evolução do saldo
    acumulado sorteio a sorteio (gráfico de linha) e o resultado financeiro
    final (gasto/ganho/saldo/ROI). Espelha calc_jogos_financeiro da Lotofácil/
    Mega-Sena, mas com acertos = interseção do jogo com as 20 dezenas
    sorteadas (esquema invertido, ver comentário acima)."""
    n = len(sorteios_p)
    resultado = []
    for nome, numeros in jogos_dict.items():
        conjunto = set(numeros)
        contagem = {20: 0, 19: 0, 18: 0, 17: 0, 16: 0, 15: 0, 0: 0}
        historico = []
        ganho = 0.0
        saldo_evolucao = []
        acumulado = 0.0
        melhor = {"acertos": None, "concurso": None, "data": None}
        melhor_prioridade = 0

        for row, s in zip(rows_p, sorteios_p):
            acertos = len(conjunto & set(s))
            premio = 0.0
            # a faixa "quinze" só existe a partir do concurso 1653 (29/04/2016)
            # — em sorteios anteriores valor_quinze é NULL porque a faixa
            # simplesmente não existia, não porque ninguém ganhou; usar isso
            # como sinal em vez de um número de concurso fixo no código.
            existe_faixa = acertos != 15 or row.get("valor_quinze") is not None
            if acertos in FAIXA_LOTOMANIA and existe_faixa:
                faixa = FAIXA_LOTOMANIA[acertos]
                contagem[acertos] += 1
                premio = _to_float(row.get(faixa["campo_valor"])) or 0.0
                historico.append({
                    "concurso": _to_int(row["concurso"]),
                    "data": row["data"],
                    "acertos": acertos,
                    "faixa": faixa["nome"],
                    "premio": round(premio, 2),
                })
                ganho += premio
                prioridade = PRIORIDADE_FAIXA_LOTOMANIA[acertos]
                if prioridade > melhor_prioridade:
                    melhor_prioridade = prioridade
                    melhor = {"acertos": acertos, "concurso": _to_int(row["concurso"]), "data": row["data"]}
            acumulado += premio - CUSTO_LOTOMANIA
            saldo_evolucao.append(round(acumulado, 2))

        gasto = round(CUSTO_LOTOMANIA * n, 2)
        ganho = round(ganho, 2)
        saldo = round(ganho - gasto, 2)
        roi = round(saldo / gasto * 100, 1) if gasto else 0.0
        total_premiado = sum(contagem.values())

        resultado.append({
            "nome": nome,
            "numeros": sorted(numeros),
            "contagem": contagem,
            "total_premiado": total_premiado,
            "pct_premiado": round(total_premiado / n * 100, 1) if n else 0.0,
            "gasto": gasto,
            "ganho": ganho,
            "saldo": saldo,
            "roi": roi,
            "melhor": melhor,
            "historico": historico,
            "saldo_evolucao": saldo_evolucao,
        })

    resultado.sort(key=lambda x: x["saldo"], reverse=True)
    return resultado


# ─── seletor de período temporal (ano / semestre / trimestre / bimestre / mês) ─
# mesma lógica de bucketing por data da Mega-Sena/Lotofácil — não depende do
# tipo de loteria

MESES_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

def calcular_bundle_periodo(rows_p, sorteios_p):
    """Estatísticas completas de um período do seletor — mesmo conjunto de
    módulos exibido para 'Todos', recalculado sobre o subconjunto de sorteios."""
    n = len(sorteios_p)
    freq = calc_frequencia(sorteios_p)
    atraso = calc_atraso(sorteios_p)
    pi = calc_pares_impares(sorteios_p)
    faixas = calc_faixas(sorteios_p)
    dist_tam, top_por_tam = calc_sequencias_consecutivas(sorteios_p)
    cooc = calc_coocorrencia(sorteios_p, top_n=20)
    janela = max(1, min(50, n))
    tendencia = calc_tendencia(sorteios_p, janela=janela)
    somas = calc_soma(sorteios_p)
    repeticao_anterior = calc_repeticao_anterior(sorteios_p)
    ciclo_medio = calc_ciclo_medio(sorteios_p)
    cooc_completo_p = calc_coocorrencia_completa(sorteios_p)
    anticorrelacao = calc_anticorrelacao(cooc_completo_p, bottom_n=15)

    return {
        "meta": {
            "total": n,
            "inicio": rows_p[0]["data"] if n else None,
            "fim": rows_p[-1]["data"] if n else None,
        },
        "frequencia": {d: c for d, c in freq.items()},
        "atraso": atraso,
        "pares_impares": pi,
        "faixas": faixas,
        "seq_dist_tamanho": {str(k): v for k, v in sorted(dist_tam.items())},
        "seq_top_por_tamanho": {str(k): v for k, v in top_por_tam.items()},
        "coocorrencia": [[[a, b], c] for (a, b), c in cooc],
        "tendencia": tendencia,
        "somas": somas,
        "repeticao_anterior": repeticao_anterior,
        "ciclo_medio": ciclo_medio,
        "anticorrelacao": [[[a, b], c] for (a, b), c in anticorrelacao],
        "blocos": calc_blocos_bundle(rows_p, sorteios_p),
        "financeiro": calc_financeiro(rows_p),
        "faixa_surpresa": calc_faixa_surpresa(rows_p),
        "jogos": calc_jogos_lotomania(JOGOS_LOTOMANIA, rows_p, sorteios_p) if JOGOS_LOTOMANIA else None,
        "sorteios_raw": sorteios_p,
        "sorteios_meta": [_meta_sorteio(r) for r in rows_p],
    }

def gerar_periodos(rows, sorteios):
    grupos = defaultdict(list)
    labels = {}
    tipos = {}

    for i, row in enumerate(rows):
        iso = data_iso_de(row)
        ano, mes = int(iso[:4]), int(iso[5:7])

        pid = f"{ano}"
        grupos[pid].append(i); labels[pid] = str(ano); tipos[pid] = "ano"

        sem = 1 if mes <= 6 else 2
        pid = f"{ano}-S{sem}"
        grupos[pid].append(i); labels[pid] = f"{sem}º Sem {ano}"; tipos[pid] = "semestre"

        tri = (mes - 1) // 3 + 1
        pid = f"{ano}-T{tri}"
        grupos[pid].append(i); labels[pid] = f"T{tri} {ano}"; tipos[pid] = "trimestre"

        bim = (mes - 1) // 2 + 1
        pid = f"{ano}-B{bim}"
        grupos[pid].append(i); labels[pid] = f"Bim{bim} {ano}"; tipos[pid] = "bimestre"

        pid = f"{ano}-M{mes:02d}"
        grupos[pid].append(i); labels[pid] = f"{MESES_PT[mes - 1]} {ano}"; tipos[pid] = "mes"

    periodos = {}
    disponiveis = []
    for pid, indices in grupos.items():
        rows_p = [rows[i] for i in indices]
        sorteios_p = [sorteios[i] for i in indices]
        periodos[pid] = calcular_bundle_periodo(rows_p, sorteios_p)
        disponiveis.append({"id": pid, "label": labels[pid], "tipo": tipos[pid], "total": len(indices)})

    disponiveis.sort(key=lambda d: d["id"])
    return periodos, disponiveis


# ─── histórico em árvore (Ano → Mês → Sorteio) ────────────────────────────────

DIAS_SEMANA_PT = {
    0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira", 3: "Quinta-feira",
    4: "Sexta-feira", 5: "Sábado", 6: "Domingo",
}
MESES_COMPLETOS_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

def calc_historico(rows: list[dict], sorteios: list[list[int]]) -> dict:
    from datetime import datetime

    arvore: dict = {}
    for row, s in zip(rows, sorteios):
        iso = data_iso_de(row)
        ano, mes = iso[:4], iso[5:7]
        dia_semana = DIAS_SEMANA_PT[datetime.strptime(iso, "%Y-%m-%d").weekday()]

        sorteio_info = {
            "concurso": _to_int(row["concurso"]),
            "data_iso": iso,
            "data_br": row["data"],
            "dia_semana": dia_semana,
            "dezenas": s,
            "acumulado": _acumulado_bool(row.get("acumulado")),
            "ganhadores": _to_int(row.get("ganhadores")),
            "premio": _to_float(row.get("valor_premio")),
            "ganhadores_surpresa": _to_int(row.get("ganhadores_surpresa")) or 0,
        }

        ano_node = arvore.setdefault(ano, {"label": ano, "total": 0, "meses": {}})
        ano_node["total"] += 1
        mes_node = ano_node["meses"].setdefault(mes, {
            "label": MESES_COMPLETOS_PT[int(mes) - 1], "total": 0, "sorteios": [],
        })
        mes_node["total"] += 1
        mes_node["sorteios"].append(sorteio_info)

    for ano_node in arvore.values():
        for mes_node in ano_node["meses"].values():
            mes_node["sorteios"].sort(key=lambda x: x["concurso"], reverse=True)

    return {
        "ultimo_concurso": _to_int(rows[-1]["concurso"]),
        "ultima_data": rows[-1]["data"],
        "total": len(rows),
        "arvore": arvore,
    }


# ─── geração do HTML ──────────────────────────────────────────────────────────
# Mesma paleta/arquitetura visual "Dark Analytics App" da Mega-Sena/Lotofácil —
# CSS quase idêntico, com acento âmbar (em vez do verde da Mega-Sena ou do roxo
# da Lotofácil) e numgrid/blocos-grid em proporção de 100 números em 5 blocos
# de 20 (grade 10×10), em vez de 60 em 6 blocos de 10 ou 25 em 5 blocos de 5.

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Lotomania BI — {titulo}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    /* base surface tokens (new) */
    --bg0: #0a0a0f;
    --bg1: #111118;
    --bg2: #1a1a24;
    --bg3: #22222f;
    --text-2: #a0a0b8;
    --text-3: #6b7280;
    --border-2: #3a3a4f;
    --shadow: 0 4px 24px rgba(0,0,0,0.4);
    --font: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
    --r-sm: 6px; --r-md: 10px; --r-lg: 14px; --r-xl: 20px;

    /* Lotomania accent — âmbar/laranja (cor de identidade deste dashboard) */
    --accent: #d97706;
    --accent2: #fbbf24;
    --accent3-amber: #b45309;
    --neon: rgba(217,119,6,0.15);

    /* status colors */
    --border: rgba(255,255,255,0.06);
    --text: #f1f0f5;
    --green: #22c55e;
    --red: #ef4444;
    --gold: #f59e0b;
    --yellow: #f59e0b;
    --blue: #3b82f6;

    /* aliases de compatibilidade — NÃO REMOVER, dezenas de regras existentes usam estes nomes */
    --bg: var(--bg0);
    --muted: var(--text-3);
    --card: var(--bg1);
    --accent3: var(--green);
    --accent4: var(--gold);
    --accent5: var(--red);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    background: radial-gradient(ellipse 1200px 800px at 15% -10%, #3d2a0f 0%, transparent 60%),
                radial-gradient(ellipse 900px 700px at 100% 0%, #3d1a0d 0%, transparent 55%),
                var(--bg);
    background-attachment: fixed;
    color: var(--text);
    font-family: var(--font);
    font-size: 14px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  @media (max-width: 640px) { body { font-size: 13px; } }
  a.voltar-menu { color: var(--accent2); text-decoration: none; font-size: 12px; font-weight: 600; display: inline-flex; align-items: center; gap: 4px; }
  a.voltar-menu:hover { text-decoration: underline; }
  header {
    position: sticky; top: 0; z-index: 50;
    background: rgba(15,16,24,0.72);
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border);
    padding: 16px 28px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  }
  header h1 { font-size: 20px; font-weight: 700; color: var(--text); letter-spacing: -0.3px; display: flex; align-items: center; gap: 8px; }
  .concurso-badge {
    margin: 0 auto; display: flex; align-items: center; gap: 8px;
    background: var(--bg3); border: 1px solid var(--border); border-radius: 999px;
    padding: 6px 16px; font-size: 12px; color: var(--muted); white-space: nowrap;
  }
  .concurso-badge b { color: var(--text); font-weight: 600; }
  .concurso-badge .dot { width: 5px; height: 5px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
  @media (max-width: 640px) {
    header { padding: 14px 16px; gap: 10px; }
    header h1 { font-size: 17px; }
    .concurso-badge { order: 3; width: 100%; justify-content: center; font-size: 11px; padding: 6px 10px; }
    #supabase-status { margin-left: 0 !important; width: 100%; }
  }
  #supabase-status { margin-left: auto; display: flex; align-items: center; gap: 10px; font-size: 12px; flex-wrap: wrap; }
  #supabase-status .msg { color: var(--muted); }
  #supabase-status .msg.novo { color: #fbbf24; font-weight: 700; }
  #supabase-status .msg.atualizado { color: var(--green); }
  #supabase-status button { flex-shrink: 0; }
  .btn-primary, .btn-secondary {
    border-radius: 8px; min-height: 40px; padding: 0 18px; font-weight: 600; font-size: 13px;
    cursor: pointer; transition: background .15s, border-color .15s, box-shadow .15s, transform .1s;
    display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  }
  .btn-primary { background: var(--accent); border: 1px solid var(--accent); color: #fff; }
  .btn-primary:hover:not(:disabled) { background: #b45309; box-shadow: 0 0 0 3px rgba(217,119,6,.25); }
  .btn-primary:active:not(:disabled) { transform: scale(.97); }
  .btn-secondary { background: transparent; border: 1px solid var(--accent); color: var(--accent2); }
  .btn-secondary:hover:not(:disabled) { background: rgba(217,119,6,.12); }
  .btn-secondary:active:not(:disabled) { transform: scale(.97); }
  .btn-primary:disabled, .btn-secondary:disabled { opacity: .5; cursor: not-allowed; transform: none; box-shadow: none; }
  @media (max-width: 640px) { .btn-primary, .btn-secondary { flex: 1 1 auto; } }
  .gh-token-dot { width: 8px; height: 8px; border-radius: 50%; background: #6b7280; display: inline-block; flex-shrink: 0; }
  .gh-token-dot.ativo { background: var(--green); box-shadow: 0 0 6px rgba(16,185,129,.7); }
  .gh-gear-btn { background: transparent !important; border: 1px solid var(--border) !important; border-radius: 6px; padding: 5px 8px !important; cursor: pointer; font-size: 13px !important; color: var(--muted) !important; }
  .gh-gear-btn:hover { border-color: var(--accent) !important; color: var(--accent2) !important; }
  .gh-feedback { margin: 10px 24px 0; padding: 10px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; }
  .gh-feedback.ok { background: rgba(16,185,129,.12); border: 1px solid var(--green); color: var(--green); }
  .gh-feedback.erro { background: rgba(239,68,68,.12); border: 1px solid var(--red); color: var(--red); }
  .gh-modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 20px; animation: ghModalFadeIn .18s ease; }
  .gh-modal { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 24px; max-width: 440px; width: 100%; max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,.5); animation: ghModalSlideUp .22s cubic-bezier(.16,1,.3,1); }
  @keyframes ghModalFadeIn { from { opacity: 0; } to { opacity: 1; } }
  @keyframes ghModalSlideUp { from { opacity: 0; transform: translateY(18px) scale(.98); } to { opacity: 1; transform: translateY(0) scale(1); } }
  @media (max-width: 640px) { .gh-modal { max-width: 90vw; padding: 20px; } }
  .gh-modal h3 { font-size: 16px; margin-bottom: 12px; color: var(--accent2); }
  .gh-modal p { font-size: 13px; color: var(--text); margin-bottom: 12px; line-height: 1.5; }
  .gh-modal-steps { background: #0f1117; border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; font-size: 12px; color: var(--muted); }
  .gh-modal-steps strong { color: var(--text); display: block; margin-bottom: 6px; }
  .gh-modal-steps ol { padding-left: 18px; }
  .gh-modal-steps li { margin-bottom: 3px; }
  .gh-modal label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .gh-token-input-wrap { display: flex; gap: 6px; margin-bottom: 12px; }
  .gh-token-input-wrap input { flex: 1; min-width: 0; background: #0f1117; border: 1px solid var(--border); border-radius: 6px; padding: 9px 12px; color: var(--text); font-size: 13px; font-family: monospace; }
  .gh-token-input-wrap input:focus { outline: none; border-color: var(--accent); }
  .gh-token-input-wrap button { background: #1e2130; border: 1px solid var(--border); border-radius: 6px; padding: 0 12px; cursor: pointer; color: var(--text); flex-shrink: 0; }
  .gh-modal-aviso { font-size: 11px; color: #fbbf24; background: rgba(245,158,11,.1); border-radius: 6px; padding: 8px 10px; margin-bottom: 14px; line-height: 1.4; }
  .gh-modal-erro { font-size: 12px; color: var(--red); margin-bottom: 10px; }
  .gh-modal-acoes { display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
  .gh-modal-acoes button { padding: 0 16px; min-height: 40px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: background .15s, transform .1s; }
  .gh-modal-acoes button:active { transform: scale(.97); }
  #gh-modal-cancelar { background: transparent; border: 1px solid var(--border); color: var(--text); }
  #gh-modal-salvar { background: var(--accent); color: #fff; }
  #gh-modal-salvar:hover { background: #b45309; }
  #gh-modal-remover { background: transparent; border: 1px solid var(--red); color: var(--red); margin-right: auto; }
  #gh-modal-remover:hover { background: rgba(239,68,68,.1); }
  @media (max-width: 640px) {
    .gh-modal-acoes { flex-direction: column-reverse; }
    .gh-modal-acoes button { width: 100%; margin-right: 0 !important; }
  }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; padding: 24px 24px 0; }
  .kpi { background: var(--bg1); border: 1px solid var(--border); border-radius: var(--r-lg); padding: 20px 24px; box-shadow: var(--shadow); transition: transform .15s, box-shadow .15s; }
  .kpi:hover { transform: translateY(-2px); box-shadow: 0 8px 28px rgba(0,0,0,.35); }
  .kpi .kpi-icon { font-size: 18px; margin-bottom: 6px; display: block; opacity: .9; }
  .kpi .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-3); font-weight: 500; line-height: 1.4; }
  .kpi .value { font-size: 28px; font-weight: 700; margin-top: 4px; color: var(--text); font-family: var(--font-mono); line-height: 1.1; }
  .kpi .sub { font-size: 12px; color: var(--text-2); margin-top: 4px; }
  @media (max-width: 640px) {
    .kpis { grid-template-columns: repeat(2, 1fr); gap: 10px; padding: 16px 16px 0; }
    .kpi { padding: 14px; border-radius: 12px; }
    .kpi .value { font-size: 24px; }
  }
  .grid { display: grid; gap: 20px; padding: 24px; }
  .grid-2 { grid-template-columns: 1fr 1fr; }
  .grid-3 { grid-template-columns: 1fr 1fr 1fr; }
  .grid-13 { grid-template-columns: 1.4fr 1fr; }
  .grid-31 { grid-template-columns: 1fr 1.4fr; }
  @media (max-width: 1024px) { .grid-3 { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 640px) { .grid-2, .grid-3, .grid-13, .grid-31 { grid-template-columns: 1fr; } .grid { gap: 14px; padding: 16px; } }
  .card { background: var(--bg1); border: 1px solid var(--border); border-radius: var(--r-lg); padding: 20px 24px; box-shadow: var(--shadow); transition: border-color .2s ease, box-shadow .2s ease; }
  .card:hover { border-color: rgba(217,119,6,.3); box-shadow: var(--shadow), 0 0 0 1px rgba(217,119,6,.08); }
  .card h2 { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text); margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
  @media (max-width: 640px) { .card { padding: 16px; border-radius: 12px; } }
  .page-content { animation: pageFadeIn .25s ease; }
  @keyframes pageFadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
  canvas { max-height: 280px; }
  .heatcell { border-radius: 10px; padding: 14px 4px; text-align: center; font-weight: 800; font-size: 16px; transition: transform .15s; cursor: default; }
  .heatcell:hover { transform: scale(1.12); z-index: 2; position: relative; }
  .heatcell .freq { font-size: 11px; font-weight: 500; opacity: .8; display: block; margin-top: 3px; }
  @media (max-width: 640px) {
    .heatcell { padding: 10px 2px; font-size: 13px; border-radius: 8px; }
    .heatcell .freq { font-size: 9px; }
  }
  /* grade interativa 10×10 (100 números, 00-99) — substitui o mapa de calor estático */
  .numgrid { display: grid; grid-template-columns: repeat(10, 1fr); gap: 6px; max-width: 640px; margin: 0 auto; }
  .numgrid-cell {
    aspect-ratio: 1; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 11px; color: #fff; cursor: pointer; border: 2px solid transparent;
    transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
  }
  .numgrid-cell:hover { transform: scale(1.1); box-shadow: 0 4px 16px rgba(217,119,6,0.3); }
  .numgrid-cell.selecionada { border-color: var(--accent2); box-shadow: 0 0 0 4px var(--neon), 0 0 18px rgba(251,191,36,.55); transform: scale(1.06); }
  .numgrid-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 18px; flex-wrap: wrap; }
  .numgrid-hint { font-size: 12px; color: var(--muted); flex: 1 1 260px; }
  @media (max-width: 640px) {
    .numgrid { max-width: 360px; gap: 4px; }
    .numgrid-cell { font-size: 9px; }
    .numgrid-footer { flex-direction: column; align-items: stretch; }
  }
  /* simulador de jogo — seleção visual por bolinhas (reusa o cálculo do
     simulador de aposta por texto, só troca o método de entrada) */
  .simball-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(32px, 1fr)); gap: 6px; margin-bottom: 16px; }
  .simball-cell {
    aspect-ratio: 1; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 11px; font-family: var(--font-mono); color: var(--text-2);
    background: var(--bg3); border: 2px solid var(--border); cursor: pointer;
    transition: transform .12s ease, background .12s ease, border-color .12s ease, color .12s ease;
    user-select: none;
  }
  .simball-cell:hover { border-color: var(--accent2); transform: scale(1.08); }
  .simball-cell.selecionada { background: var(--accent); border-color: var(--accent); color: #fff; transform: scale(1.05); }
  .simball-cell.desabilitada { opacity: .35; cursor: not-allowed; }
  .simball-cell.desabilitada:hover { transform: none; border-color: var(--border); }
  .simball-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
  .simball-contador { font-size: 13px; color: var(--text-2); font-weight: 600; }
  .simball-contador.completo { color: var(--accent2); }
  .tabs { display: flex; gap: 0; margin-bottom: 14px; flex-wrap: wrap; border-bottom: 1px solid var(--border); }
  .tab { padding: 7px 16px; border-radius: 0; border: none; border-bottom: 2px solid transparent; background: transparent; color: var(--muted); cursor: pointer; font-size: 12px; font-weight: 600; transition: color .15s, border-bottom-color .15s; margin-bottom: -1px; }
  .tab.active { background: transparent; border-bottom-color: var(--accent); color: var(--text); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--text-3); font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; padding: 10px 14px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--bg1); z-index: 1; }
  td { padding: 10px 14px; color: var(--text-2); font-family: var(--font-mono); font-size: 12px; border-bottom: 1px solid var(--border); }
  .jogos-tabela td:nth-child(2), .jogos-tabela th:nth-child(2) { font-family: var(--font); }
  tbody tr:nth-child(even) td { background: rgba(255,255,255,.025); }
  tbody tr:hover td { background: var(--bg2); }
  tr:last-child td { border-bottom: none; }
  @media (max-width: 640px) { table { font-size: 12px; } td, th { padding: 7px 6px; } }
  .badge { display: inline-flex; align-items: center; gap: 3px; background: #1e2130; border-radius: 4px; padding: 2px 6px; font-size: 11px; font-weight: 700; }
  .badge.up { color: var(--green); }
  .badge.down { color: var(--red); }
  .badge.flat { color: var(--muted); }
  .seq-tag { display: inline-block; background: var(--bg3); border: 1px solid var(--border-2); color: var(--accent2); border-radius: var(--r-sm); padding: 2px 7px; font-weight: 600; font-size: 12px; margin-right: 3px; font-family: var(--font-mono); }
  .bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
  .bar-row .label { width: 90px; font-size: 12px; color: var(--muted); text-align: right; flex-shrink: 0; }
  .bar-row .bar { height: 18px; border-radius: 4px; background: var(--accent); min-width: 4px; transition: width .4s; }
  .bar-row .val { font-size: 12px; color: var(--text); flex-shrink: 0; }
  footer { text-align: center; color: var(--muted); font-size: 11px; padding: 16px; border-top: 1px solid var(--border); margin-top: 8px; }
  .mini-stats { display: flex; gap: 24px; margin-top: 14px; font-size: 12px; color: var(--muted); flex-wrap: wrap; }
  .mini-stats b { color: var(--text); font-size: 16px; display: block; }
  .status-tag { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px; }
  .status-tag.dentro { background: rgba(16,185,129,.15); color: var(--green); }
  .status-tag.alem { background: rgba(239,68,68,.15); color: var(--red); }
  /* grade labeled heatmap (10 colunas) */
  .grade-wrap { display: grid; grid-template-columns: 32px repeat(10, 1fr); gap: 4px; align-items: center; }
  .grade-wrap .rowhead, .grade-wrap .colhead { text-align: center; font-size: 10px; color: var(--muted); font-weight: 700; }
  .grade-wrap .heatcell { padding: 8px 2px; font-size: 12px; }
  /* blocos — ranking de frequência individual por número (5 blocos de 20) */
  .blocos-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
  @media (max-width: 1200px) { .blocos-grid { grid-template-columns: repeat(3, 1fr); } }
  @media (max-width: 900px) { .blocos-grid { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 560px) { .blocos-grid { grid-template-columns: 1fr; } }
  .bloco-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--r-md); padding: 14px; }
  .bloco-card h3 { font-size: 12px; font-weight: 700; color: var(--accent2); margin-bottom: 12px; text-transform: uppercase; letter-spacing: .5px; }
  .bloco-rank-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 11px; }
  .bloco-rank-row .medalha { width: 14px; flex-shrink: 0; text-align: center; font-size: 12px; }
  .bloco-num { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border-radius: var(--r-sm); background: var(--bg0); border: 1px solid var(--border); font-weight: 700; font-size: 11px; flex-shrink: 0; font-family: var(--font-mono); }
  .bloco-num.top { background: linear-gradient(135deg,var(--gold),var(--accent2)); color: #1a1300; border-color: var(--gold); }
  .bloco-num.bottom { background: rgba(239,68,68,.15); color: #fca5a5; border-color: rgba(239,68,68,.5); }
  .bloco-bar-wrap { flex: 1; background: var(--bg0); border-radius: 4px; height: 12px; overflow: hidden; }
  .bloco-bar { height: 100%; background: var(--accent); border-radius: 4px; }
  .bloco-rank-row.top .bloco-bar { background: linear-gradient(90deg,var(--gold),var(--accent2)); }
  .bloco-rank-row.bottom .bloco-bar { background: rgba(239,68,68,.6); }
  .bloco-rank-row .contagem { width: 82px; text-align: right; color: var(--text-3); flex-shrink: 0; font-family: var(--font-mono); }
  .window-btns { display: flex; gap: 6px; margin-bottom: 14px; }
  .hotcold-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(80px, 1fr)); gap: 8px; margin-top: 4px; }
  .hotcold-card { border-radius: 8px; padding: 10px 6px; text-align: center; border: 1px solid var(--border); background: #1e2130; }
  .hotcold-card.quente { background: rgba(239,68,68,.15); border-color: var(--red); }
  .hotcold-card.frio { background: rgba(251,191,36,.15); border-color: var(--accent2); }
  .hotcold-card .num { font-size: 16px; font-weight: 700; }
  .hotcold-card .status { font-size: 14px; }
  .hotcold-card .pct { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .hotcold-legend { display: flex; gap: 18px; margin-bottom: 12px; font-size: 12px; color: var(--muted); }
  .money-pos { color: var(--green); font-weight: 700; }
  .money-neg { color: var(--red); font-weight: 700; }
  /* card "Faixa Surpresa" (faixa 7, 0 acertos) — destaque visual distinto */
  .surpresa-card { border-color: rgba(217,119,6,.4); background: linear-gradient(135deg, rgba(217,119,6,.10), var(--card) 55%); }
  .surpresa-card:hover { border-color: rgba(217,119,6,.6); }
  .surpresa-sub { color: var(--muted); font-size: 12px; margin-bottom: 14px; line-height: 1.5; }
  /* card "Sorteios repetidos" */
  .repet-ok { display: flex; align-items: flex-start; gap: 10px; padding: 4px 0; }
  .repet-ok .icone { font-size: 20px; line-height: 1; }
  .repet-ok p { margin: 0; font-size: 13px; color: var(--text); }
  .repet-ok .prob { margin-top: 6px; font-size: 12px; color: var(--muted); }
  .repet-item { border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; background: var(--bg3); }
  .repet-item:last-child { margin-bottom: 0; }
  .repet-item-titulo { font-size: 12px; font-weight: 700; color: var(--accent2); margin-bottom: 8px; }
  .repet-numeros { font-family: monospace; font-size: 13px; color: var(--text); letter-spacing: .5px; margin-bottom: 10px; }
  .repet-sorteio { font-size: 12px; color: var(--muted); padding: 3px 0; }
  .repet-sorteio b { color: var(--text); }
  /* seletor de período — card com grupos empilhados por tipo */
  .period-selector-wrap { padding: 14px 24px 0; position: sticky; top: 65px; z-index: 40; }
  .period-card { background: var(--bg1); border: 1px solid var(--border); border-radius: var(--r-xl); padding: 20px 24px; box-shadow: var(--shadow); }
  .period-card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
  .period-card-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-3); font-weight: 600; }
  .period-row { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; }
  .period-row:last-child { margin-bottom: 0; }
  .period-row .tipo-label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; width: 90px; flex-shrink: 0; padding-top: 5px; }
  .period-row-scroll { display: flex; gap: 6px; overflow-x: auto; flex-wrap: nowrap; scrollbar-width: none; -ms-overflow-style: none; padding-bottom: 2px; }
  .period-row-scroll::-webkit-scrollbar { display: none; }
  .period-btn {
    background: var(--bg3); border: 1px solid var(--border); border-radius: var(--r-sm);
    padding: 5px 12px; font-size: 12px; color: var(--text-2); cursor: pointer;
    flex-shrink: 0; white-space: nowrap; transition: all .15s;
  }
  .period-btn:hover { border-color: var(--accent2); color: var(--accent2); }
  .period-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }
  .period-btn:disabled { opacity: .35; cursor: not-allowed; }
  .period-btn:disabled:hover { border-color: var(--border); }
  /* nível 2 (radio "Ano completo/Semestre/Trimestre/Bimestre/Mês") e a
     animação de entrada dos níveis 2/3 quando aparecem em cascata */
  .period-nivel2-opcoes { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
  .period-radio { display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 12px; color: var(--text); }
  .period-radio input { accent-color: var(--accent); cursor: pointer; }
  .period-nivel { animation: periodNivelIn .2s ease; }
  @keyframes periodNivelIn { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
  /* Ano(s) multi-select (checkboxes) + "Ver todos os anos" */
  .period-anos-wrap { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
  .period-ano-check { display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 12px; color: var(--text-2); background: var(--bg3); border: 1px solid var(--border); border-radius: var(--r-sm); padding: 5px 12px; transition: all .15s; }
  .period-ano-check:hover { border-color: var(--accent2); color: var(--accent2); }
  .period-ano-check.selecionado,
  .period-ano-check:has(input:checked) { background: var(--accent); border-color: var(--accent); color: #fff; }
  .period-ano-check input { accent-color: #fff; cursor: pointer; }
  .period-ver-todos-btn { background: transparent; border: none; color: var(--accent2); font-size: 11px; cursor: pointer; padding: 4px 0; text-decoration: underline; }
  @media (max-width: 640px) {
    .period-selector-wrap { top: 57px; padding: 10px 16px 0; }
    .period-card { padding: 12px 14px; }
    .period-row { flex-direction: column; align-items: stretch; gap: 4px; }
    .period-row .tipo-label { width: auto; padding-top: 0; }
    .period-btn { font-size: 11px; padding: 3px 8px; }
    .period-nivel2-opcoes { gap: 12px; }
    .period-anos-wrap { gap: 8px; }
  }
  .periodo-banner { margin: 12px 24px 0; padding: 8px 14px; background: rgba(217,119,6,0.08); border: 1px solid rgba(217,119,6,0.2); border-radius: var(--r-sm); color: var(--accent2); font-size: 12px; font-weight: 600; }
  .update-banner { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .update-banner button { background: #f59e0b; border: none; border-radius: 8px; min-height: 36px; padding: 0 16px; color: #1a1300; font-weight: 700; cursor: pointer; font-size: 12px; flex-shrink: 0; transition: background .15s, transform .1s; }
  .update-banner button:hover { background: #fbbf24; }
  .update-banner button:active { transform: scale(.97); }
  .fin-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; }
  .fin-item { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--r-md); padding: 14px; }
  .fin-item .label { color: var(--text-3); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 6px; }
  .fin-item .value { font-size: 18px; font-weight: 700; color: var(--accent2); font-family: var(--font-mono); }
  .fin-item .sub { font-size: 11px; color: var(--text-2); margin-top: 4px; }
  /* Meus Jogos — tabela comparativa */
  .jogos-resumo-titulo { font-size: 14px; font-weight: 700; color: var(--accent2); margin-bottom: 10px; }
  .jogos-tabela th[data-col] { cursor: pointer; user-select: none; white-space: nowrap; }
  .jogos-tabela th[data-col]:hover { color: var(--text); }
  .jogos-tabela th .sort-arrow { display: inline-block; width: 10px; opacity: .6; }
  .jogos-row { cursor: pointer; }
  .jogos-row.saldo-pos td { background: rgba(34,197,94,.04); }
  .jogos-row.saldo-neg td { background: rgba(239,68,68,.04); }
  .jogos-row:hover td { filter: brightness(1.15); }
  .jogos-row .jogos-dezenas { font-family: var(--font-mono); font-size: 11px; color: var(--text-3); }
  .jogos-detail-row td { padding: 0; border-bottom: 1px solid var(--border); }
  .jogos-detail-inner { max-height: 0; overflow: hidden; transition: max-height .25s ease; padding: 0 16px; }
  .jogos-detail-inner.aberto { max-height: 900px; padding: 16px; }
  .jogos-detail-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 20px; margin-bottom: 16px; }
  @media (max-width: 900px) { .jogos-detail-grid { grid-template-columns: 1fr; } }
  .jogos-detail-grid canvas { max-height: 200px; }
  .jogos-detail-titulo { font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: var(--text-3); margin-bottom: 10px; }
  .jogos-hist-surpresa td { color: var(--accent2); font-weight: 700; }
  .jogos-badge-surpresa { margin-left: 4px; }
  /* Simulador de apostas */
  .sim-box { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
  .sim-box input { flex: 1; min-width: 260px; background: var(--bg3); border: 1px solid var(--border); border-radius: var(--r-sm); padding: 9px 12px; color: var(--text); font-size: 13px; font-family: var(--font-mono); }
  .sim-box input:focus { outline: none; border-color: var(--accent); }
  .sim-box button { background: var(--accent); border: none; border-radius: var(--r-sm); padding: 9px 20px; color: #fff; font-weight: 600; cursor: pointer; font-size: 13px; }
  .sim-box button:hover { background: var(--accent3-amber); }
  .sim-error { color: var(--red); font-size: 12px; margin: -6px 0 12px; }
  .sim-qtd-selector { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 16px; font-size: 13px; }
  .sim-qtd-selector label { display: flex; align-items: center; gap: 5px; cursor: pointer; color: var(--text); }
  .sim-qtd-label { font-weight: 700; color: var(--text-3); text-transform: uppercase; font-size: 11px; letter-spacing: .5px; }
  .sim-jogos-lista { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
  .sim-jogo-row { display: flex; align-items: center; gap: 8px; }
  .sim-jogo-label { width: 62px; flex-shrink: 0; font-size: 12px; color: var(--text-3); font-weight: 600; }
  .sim-jogo-input { flex: 1; min-width: 0; background: var(--bg3); border: 1px solid var(--border); border-radius: var(--r-sm); padding: 8px 12px; color: var(--text); font-size: 13px; font-family: var(--font-mono); }
  .sim-jogo-input:focus { outline: none; border-color: var(--accent); }
  .sim-jogo-badge { min-width: 96px; text-align: center; font-size: 11px; font-weight: 700; padding: 4px 8px; border-radius: var(--r-sm); flex-shrink: 0; border: 1px solid var(--border); color: var(--text-3); }
  .sim-jogo-badge.ok { background: rgba(34,197,94,.15); color: var(--green); border-color: rgba(34,197,94,.4); }
  .sim-jogo-badge.parcial { background: rgba(245,158,11,.15); color: var(--gold); border-color: rgba(245,158,11,.4); }
  .sim-jogo-badge.erro { background: rgba(239,68,68,.15); color: var(--red); border-color: rgba(239,68,68,.4); }
  .sim-jogo-remove { background: transparent; border: 1px solid var(--border); border-radius: var(--r-sm); padding: 6px 10px; cursor: pointer; color: var(--text-3); font-size: 13px; flex-shrink: 0; }
  .sim-jogo-remove:hover { border-color: var(--red); color: var(--red); }
  .sim-acoes { display: flex; gap: 10px; margin-bottom: 14px; }
  .sim-acoes button { background: var(--accent); border: none; border-radius: var(--r-sm); padding: 9px 18px; color: #fff; font-weight: 600; cursor: pointer; font-size: 13px; }
  .sim-acoes button:hover { background: var(--accent3-amber); }
  .sim-acoes #sim-btn-add { background: transparent; border: 1px solid var(--border); color: var(--text); }
  .sim-acoes #sim-btn-add:hover { border-color: var(--accent); color: var(--accent2); }
  .sim-aviso { color: var(--gold); font-size: 12px; margin-bottom: 10px; }
  .sim-compare-row.destaque-ouro td { background: rgba(245,158,11,.12); }
  .sim-trofeu { margin-left: 4px; }
  .sim-detalhe-toggle { cursor: pointer; color: var(--accent2); font-size: 11px; background: none; border: none; padding: 0; text-decoration: underline; }
  .sim-detalhe-painel { display: none; padding: 10px 0 4px; }
  .sim-detalhe-painel.aberto { display: block; }
  .sim-detalhe-item { display: inline-block; margin: 2px 6px 2px 0; padding: 3px 8px; border-radius: var(--r-sm); background: var(--bg3); font-size: 11px; color: var(--text); font-family: var(--font-mono); }
  .sim-copiar-btn { margin-top: 12px; background: transparent; border: 1px solid var(--border); border-radius: var(--r-sm); padding: 8px 16px; color: var(--text); cursor: pointer; font-size: 12px; }
  .sim-copiar-btn:hover { border-color: var(--accent); color: var(--accent2); }
  .page-tabs { display: flex; gap: 0; padding: 0 24px; border-bottom: 1px solid var(--border); overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: thin; }
  .page-tab { padding: 14px 20px; border: none; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: var(--muted); cursor: pointer; font-size: 13px; font-weight: 600; white-space: nowrap; flex-shrink: 0; transition: color .15s, border-bottom-color .15s; margin-bottom: -1px; }
  .page-tab:hover { color: var(--text); background: transparent; }
  .page-tab.active { color: var(--accent2); border-bottom-color: var(--accent); box-shadow: none; }
  @media (max-width: 640px) {
    .page-tabs { padding: 0 16px; }
    .page-tab { padding: 12px 14px; font-size: 13px; }
  }
  .hist-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 20px 24px 0; }
  .hist-header-info { display: flex; gap: 28px; flex-wrap: wrap; }
  .hist-header-info .label { display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
  .hist-header-info b { font-size: 18px; color: var(--accent2); }
  .hist-controles { margin: 20px 24px 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; align-items: end; }
  @media (max-width: 900px) { .hist-controles { grid-template-columns: 1fr; } }
  .hist-controle-grupo label { display: block; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 6px; }
  .hist-ano { border-bottom: 1px solid var(--border); }
  .hist-ano:last-child { border-bottom: none; }
  .hist-ano-header, .hist-mes-header { display: flex; align-items: center; gap: 10px; padding: 12px 8px; cursor: pointer; user-select: none; }
  .hist-ano-header:hover, .hist-mes-header:hover { background: #1e2130; border-radius: 8px; }
  .hist-arrow { display: inline-block; width: 14px; color: var(--muted); transition: transform .2s; flex-shrink: 0; }
  .hist-arrow.open { transform: rotate(90deg); }
  .hist-ano-header .hist-titulo { font-size: 15px; font-weight: 700; color: var(--text); }
  .hist-mes-header .hist-titulo { font-size: 13px; font-weight: 600; color: #cbd5e1; }
  .hist-contador { color: var(--muted); font-size: 12px; margin-left: auto; }
  .hist-meses, .hist-sorteios { max-height: 0; overflow: hidden; transition: max-height .28s ease; }
  .hist-meses { padding-left: 14px; }
  .hist-mes { border-top: 1px solid #1e2130; }
  .hist-sorteios { padding-left: 20px; }
  .hist-sorteio-row { display: flex; align-items: center; gap: 10px; padding: 7px 8px; cursor: pointer; border-radius: 6px; font-size: 12px; color: var(--text); }
  .hist-sorteio-row:hover { background: #1e2130; }
  .hist-sorteio-row .hist-concurso { color: var(--muted); }
  .hist-sorteio-row.hist-match { background: rgba(16,185,129,.12); }
  .hist-sorteio-row.hist-filtrado-fora { display: none; }
  .hist-mes.hist-fora-periodo, .hist-ano.hist-fora-periodo { display: none; }
  .hist-sorteio-row.hist-highlight { animation: histFlash 1.6s ease; }
  @keyframes histFlash { 0%, 100% { background: transparent; } 30% { background: rgba(217,119,6,.35); } }
  .hist-sorteio-detail { max-height: 0; overflow: hidden; transition: max-height .25s ease; margin-left: 24px; }
  .hist-detail-inner { padding: 10px 12px 14px; }
  .hist-detail-titulo { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
  .hist-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
  .hist-badge { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; background: var(--bg3); border: 1px solid var(--border-2); font-family: var(--font-mono); font-size: 12px; font-weight: 600; color: var(--text); margin: 2px; }
  .hist-detail-meta { display: flex; gap: 20px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-top: 6px; }
  .hist-detail-meta b { color: var(--text); }
</style>
</head>
<body>
<header>
  <h1>🎯 Lotomania BI</h1>
  <div class="concurso-badge"><span class="dot"></span><b>{subtitulo}</b></div>
  <a class="voltar-menu" href="index.html">← Menu</a>
  <div id="supabase-status" style="display:none;"></div>
</header>

<div class="period-selector-wrap">
  <div class="period-card">
    <div class="period-card-header">
      <span class="period-card-title">Período de análise</span>
      <button type="button" class="period-btn active" id="period-todos-btn" data-periodo="__todos__">Todos os dados</button>
    </div>
    <div id="period-selector"></div>
  </div>
</div>
<div class="periodo-banner" id="periodo-banner" style="display:none;"></div>
<div class="periodo-banner update-banner" id="update-banner" style="display:none;"></div>
<div class="gh-feedback" id="gh-feedback" style="display:none;"></div>

<div class="gh-modal-overlay" id="gh-modal-overlay" style="display:none;">
  <div class="gh-modal">
    <h3>🔑 Configure seu GitHub Token</h3>
    <p>Para atualizar direto daqui, você precisa de um token do GitHub com permissão "workflow".</p>
    <div class="gh-modal-steps">
      <strong>Como criar:</strong>
      <ol>
        <li>Acesse github.com/settings/tokens</li>
        <li>"Generate new token (classic)"</li>
        <li>Marque apenas a permissão: workflow</li>
        <li>Clique em Generate e copie o código</li>
      </ol>
    </div>
    <label for="gh-token-input">Cole seu token aqui:</label>
    <div class="gh-token-input-wrap">
      <input type="password" id="gh-token-input" placeholder="ghp_..." autocomplete="off"/>
      <button type="button" id="gh-token-toggle">👁</button>
    </div>
    <div class="gh-modal-aviso">⚠️ Salvo apenas no seu navegador (localStorage). Nunca enviado para nenhum servidor nosso — só direto para a API do GitHub. Esse token é compartilhado com os dashboards da Lotofácil e da Mega-Sena (mesmo repositório).</div>
    <div class="gh-modal-erro" id="gh-modal-erro" style="display:none;"></div>
    <div class="gh-modal-acoes">
      <button type="button" id="gh-modal-remover" style="display:none;">Remover token</button>
      <button type="button" id="gh-modal-cancelar">Cancelar</button>
      <button type="button" id="gh-modal-salvar">Salvar e atualizar</button>
    </div>
  </div>
</div>

<div class="page-tabs">
  <button class="page-tab active" id="page-tab-geral">Análise Geral</button>
  <button class="page-tab" id="page-tab-blocos">Blocos</button>
  <button class="page-tab" id="page-tab-historico">Histórico</button>
  <button class="page-tab" id="page-tab-jogos">Meus Jogos</button>
</div>

<div id="page-geral" class="page-content">
<!-- Grade interativa 10×10 (100 números, 00-99) — substitui o mapa de calor
     estático; clique filtra os gráficos abaixo -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card numgrid-card">
    <h2>🔢 Grade interativa — clique nas dezenas para filtrar os gráficos</h2>
    <div class="numgrid" id="numgrid"></div>
    <div class="numgrid-footer">
      <span class="numgrid-hint" id="numgrid-hint">Clique em uma ou mais dezenas para filtrar todos os gráficos abaixo pela combinação escolhida.</span>
      <button type="button" class="btn-secondary" id="numgrid-limpar" style="display:none;">Limpar seleção</button>
    </div>
  </div>
</div>
<div class="kpis">
  <div class="kpi"><span class="kpi-icon">📊</span><div class="label">Sorteios analisados</div><div class="value" id="kpi-total">—</div></div>
  <div class="kpi"><span class="kpi-icon">🔥</span><div class="label">Número mais frequente</div><div class="value" id="kpi-top1">—</div><div class="sub" id="kpi-top1-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">❄️</span><div class="label">Número menos frequente</div><div class="value" id="kpi-bot1">—</div><div class="sub" id="kpi-bot1-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">⚖️</span><div class="label">Média pares/sorteio</div><div class="value" id="kpi-pares">—</div><div class="sub">de 20 dezenas</div></div>
  <div class="kpi"><span class="kpi-icon">➕</span><div class="label">Soma média das 20 dez.</div><div class="value" id="kpi-soma">—</div></div>
  <div class="kpi"><span class="kpi-icon">🔗</span><div class="label">Maior sequência vista</div><div class="value" id="kpi-seq">—</div><div class="sub">números consecutivos</div></div>
  <div class="kpi"><span class="kpi-icon">🔁</span><div class="label">Repetições do concurso anterior</div><div class="value" id="kpi-repeticao">—</div><div class="sub" id="kpi-repeticao-sub"></div></div>
</div>

<!-- Resumo financeiro do período selecionado -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>💰 Resumo financeiro (prêmios da faixa 1 — 20 acertos)</h2>
    <div class="fin-grid" id="financeiro-resumo"></div>
  </div>
</div>

<!-- Faixa Surpresa (faixa 7, 0 acertos) — exclusiva da Lotomania -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card surpresa-card">
    <h2>0️⃣ Faixa Surpresa — acertar ZERO dezenas também premia</h2>
    <p class="surpresa-sub">A Lotomania é a única loteria da Caixa em que não acertar NENHUMA das 20 dezenas sorteadas também paga prêmio (faixa 7, "surpresa").</p>
    <div class="fin-grid" id="surpresa-resumo"></div>
  </div>
</div>

<!-- Frequência + Atraso + Pares/Ímpares -->
<div class="grid grid-3">
  <div class="card">
    <h2>📊 Frequência por dezena (00–99)</h2>
    <div class="tabs" id="freq-tabs"></div>
    <canvas id="chartFreq"></canvas>
  </div>
  <div class="card">
    <h2>⏱️ Atraso atual — sorteios sem aparecer</h2>
    <canvas id="chartAtraso"></canvas>
  </div>
  <div class="card">
    <h2>⚖️ Pares vs. Ímpares</h2>
    <canvas id="chartPI"></canvas>
    <div style="margin-top:14px; display:flex; gap:24px; justify-content:center; font-size:13px;">
      <span><span style="color:#d97706">■</span> Pares: <b id="pct-pares">—</b></span>
      <span><span style="color:#fbbf24">■</span> Ímpares: <b id="pct-impares">—</b></span>
    </div>
  </div>
</div>

<!-- Distribuição por bloco + Soma -->
<div class="grid grid-2">
  <div class="card">
    <h2>📈 Distribuição por bloco (média por sorteio)</h2>
    <canvas id="chartFaixas"></canvas>
  </div>
  <div class="card">
    <h2>➕ Distribuição da soma das 20 dezenas</h2>
    <canvas id="chartSoma"></canvas>
  </div>
</div>

<!-- Sequências consecutivas -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🔗 Sequências de números consecutivos (tamanho 2, 3 e 4)</h2>
    <div class="tabs" id="seq-tabs"></div>
    <div id="seq-contents"></div>
  </div>
</div>

<!-- Co-ocorrência + Anti-correlação + Tendência -->
<div class="grid grid-3">
  <div class="card">
    <h2>🤝 Top 20 pares que mais saíram juntos</h2>
    <div id="cooc-list" style="overflow-y:auto; max-height:300px;"></div>
  </div>
  <div class="card">
    <h2>🙅 Top 15 pares que menos saíram juntos</h2>
    <div id="anticorr-list" style="overflow-y:auto; max-height:300px;"></div>
  </div>
  <div class="card">
    <h2>📉 Tendência — últimos 50 vs. total (% de aparição)</h2>
    <canvas id="chartTend"></canvas>
  </div>
</div>

<!-- Ciclo médio + Repetição do concurso anterior -->
<div class="grid grid-2">
  <div class="card">
    <h2>🔄 Ciclo médio por dezena (curto → longo)</h2>
    <div id="ciclo-list" style="overflow-y:auto; max-height:340px;"></div>
  </div>
  <div class="card">
    <h2>🔁 Repetição do concurso anterior</h2>
    <canvas id="chartRepeticao"></canvas>
    <div class="mini-stats">
      <div>Média<b id="rep-media">—</b></div>
      <div>Mínimo<b id="rep-min">—</b></div>
      <div>Máximo<b id="rep-max">—</b></div>
    </div>
  </div>
</div>

<!-- Números quentes e frios -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🌡️ Números quentes e frios</h2>
    <div class="window-btns" id="hotcold-btns">
      <button class="tab" data-janela="15">Últimos 15</button>
      <button class="tab active" data-janela="30">Últimos 30</button>
      <button class="tab" data-janela="50">Últimos 50</button>
    </div>
    <div class="hotcold-legend">
      <span>🔥 Quente: ≥20% acima da média do período</span>
      <span>❄️ Frio: ≥20% abaixo da média do período</span>
      <span>~ Normal: dentro da faixa</span>
    </div>
    <div class="hotcold-grid" id="hotcold-grid"></div>
  </div>
</div>

<!-- Sorteios repetidos — curiosidade estatística, sempre histórico completo -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="repeticoes-card">
    <h2>🔄 Sorteios repetidos</h2>
    <div id="repeticoes-conteudo"></div>
  </div>
</div>

</div><!-- /page-geral -->

<div id="page-blocos" class="page-content" style="display:none;">
<div class="kpis">
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco A (01-20) — campeão</div><div class="value" id="kpi-bloco-a">—</div><div class="sub" id="kpi-bloco-a-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco B (21-40) — campeão</div><div class="value" id="kpi-bloco-b">—</div><div class="sub" id="kpi-bloco-b-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco C (41-60) — campeão</div><div class="value" id="kpi-bloco-c">—</div><div class="sub" id="kpi-bloco-c-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco D (61-80) — campeão</div><div class="value" id="kpi-bloco-d">—</div><div class="sub" id="kpi-bloco-d-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco E (81-00) — campeão</div><div class="value" id="kpi-bloco-e">—</div><div class="sub" id="kpi-bloco-e-sub"></div></div>
</div>

<!-- 1. Ranking de frequência individual por número, dentro de cada bloco -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🏅 Ranking de frequência por bloco (01-20, 21-40, 41-60, 61-80, 81-00)</h2>
    <div class="blocos-grid" id="blocos-ranking-grid"></div>
  </div>
</div>

<!-- 2. Tabela comparativa campeão/lanterna -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>📋 Comparativo dos 5 blocos — campeão e lanterna</h2>
    <div id="blocos-campeoes" style="overflow-x:auto;"></div>
  </div>
</div>

<!-- 3. Combinações de distribuição mais frequentes + co-ocorrência entre blocos -->
<div class="grid grid-2">
  <div class="card">
    <h2>🔢 Combinações de distribuição mais frequentes (A-B-C-D-E)</h2>
    <div id="blocos-combinacoes" style="overflow-x:auto;"></div>
  </div>
  <div class="card">
    <h2>🔗 Co-ocorrência entre blocos (≥4 dezenas no mesmo sorteio)</h2>
    <div class="grade-wrap" id="blocos-coocorrencia" style="grid-template-columns: 40px repeat(5, 1fr);"></div>
  </div>
</div>

<!-- 4. Blocos por período (histórico completo em "Todos", condensado ao
     período ativo quando há filtro, oculto quando o filtro é 1 mês só) -->
<div class="grid" style="grid-template-columns: 1fr;" id="blocos-mensal-wrap">
  <div class="card">
    <h2 id="blocos-heatmap-titulo">🗓️ Média por mês — histórico completo</h2>
    <div id="blocos-heatmap-periodo" style="overflow-x:auto;"></div>
  </div>
</div>
</div><!-- /page-blocos -->

<div id="page-historico" class="page-content" style="display:none;">

<div class="hist-header">
  <div class="hist-header-info">
    <div><span class="label">Último sorteio</span><b id="hist-ultimo">—</b></div>
    <div><span class="label">Total</span><b id="hist-total">—</b></div>
  </div>
</div>

<div class="card hist-controles">
  <div class="hist-controle-grupo">
    <label for="hist-busca-concurso">Buscar concurso</label>
    <div class="sim-box" style="margin-bottom:0; display:flex; gap:10px;">
      <input type="text" id="hist-busca-concurso" placeholder="ex: 2500" style="flex:1; min-width:0; background:#0f1117; border:1px solid var(--border); border-radius:6px; padding:9px 12px; color:var(--text); font-size:13px;"/>
      <button id="hist-btn-buscar" class="btn-secondary">Ir</button>
    </div>
    <div class="sim-error" id="hist-busca-erro" style="display:none; color:var(--red); font-size:12px; margin-top:6px;"></div>
  </div>
  <div class="hist-controle-grupo">
    <label for="hist-filtro-dezena">Filtrar por dezena (00-99)</label>
    <div class="sim-box" style="margin-bottom:0; display:flex; gap:10px;">
      <input type="text" id="hist-filtro-dezena" placeholder="ex: 07" style="flex:1; min-width:0; background:#0f1117; border:1px solid var(--border); border-radius:6px; padding:9px 12px; color:var(--text); font-size:13px;"/>
      <button id="hist-btn-limpar-filtro" class="btn-secondary">Limpar</button>
    </div>
  </div>
  <div class="hist-controle-grupo">
    <label>&nbsp;</label>
    <button class="tab" id="hist-btn-expandir-tudo">Expandir tudo</button>
  </div>
</div>

<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <div id="historico-arvore"></div>
  </div>
</div>

</div><!-- /page-historico -->

<div id="page-jogos" class="page-content" style="display:none;">

<!-- Meus jogos — validador financeiro (esquema invertido: acertos = quantas
     das 20 sorteadas caem dentro das 50 apostadas; 0 acertos também premia) -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="jogos-header-card">
    <h2>🎯 Meus Jogos — Lotomania</h2>
    <p style="color:var(--text-3); font-size:11px; margin:-4px 0 12px;">
      ℹ️ A faixa de 15 acertos só existe a partir do concurso 1.653 (29/04/2016) — confirmado via API real da Caixa. Sorteios anteriores a essa data não tinham essa faixa, então acertar 15 dezenas neles não é contado como premiação aqui.
    </p>
    <div id="jogos-resumo"></div>
  </div>
</div>

<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="jogos-card">
    <h2>📊 Tabela comparativa</h2>
    <p style="color:var(--muted); font-size:12px; margin-bottom:12px;">Clique numa linha para ver o histórico completo de sorteios em que o jogo pontuou. Clique nos títulos das colunas pra ordenar.</p>
    <div id="jogos-tabela-wrap" style="overflow-x:auto;"></div>
  </div>
</div>

<!-- Simulador de Jogo — seleção visual por bolinhas -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🎱 Simulador de Jogo</h2>
    <p style="color:var(--muted); font-size:12px; margin-bottom:14px;">
      Clique nas dezenas para montar seu jogo (50 números de 00 a 99) e veja como ele teria se saído contra o período selecionado.
    </p>
    <div class="simball-grid" id="simball-grid"></div>
    <div class="simball-footer">
      <span class="simball-contador" id="simball-contador">0/50 selecionados</span>
      <div style="display:flex; gap:8px;">
        <button class="btn-secondary" id="simball-limpar" type="button">Limpar</button>
        <button class="btn-primary" id="simball-btn" type="button" disabled>Simular ▶</button>
      </div>
    </div>
    <div class="sim-error" id="simball-error" style="display:none;"></div>
    <div id="simball-result"></div>
  </div>
</div>

<!-- Simulador de apostas -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🎰 Simulador de apostas</h2>
    <p style="color:var(--muted); font-size:12px; margin-bottom:14px;">
      Informe exatamente 50 números entre 00 e 99. Aceita separadores flexíveis — espaço, vírgula, ponto, traço, ponto e vírgula, ou qualquer mistura (ex: "00, 02 05-06;07 09.11 ...").
    </p>

    <div class="sim-qtd-selector" id="sim-qtd-selector">
      <span class="sim-qtd-label">Simular</span>
      <label><input type="radio" name="sim-qtd" value="1" checked> 1 jogo</label>
      <label><input type="radio" name="sim-qtd" value="5"> 5 jogos</label>
      <label><input type="radio" name="sim-qtd" value="10"> 10 jogos</label>
      <label><input type="radio" name="sim-qtd" value="15"> 15 jogos</label>
      <label><input type="radio" name="sim-qtd" value="30"> 30 jogos</label>
    </div>

    <div id="sim-jogos-lista" class="sim-jogos-lista"></div>

    <div class="sim-acoes">
      <button id="sim-btn-add" type="button">➕ Adicionar jogo</button>
      <button id="sim-btn" type="button">Verificar</button>
    </div>

    <div class="sim-error" id="sim-error" style="display:none;"></div>
    <div class="sim-aviso" id="sim-periodo-aviso" style="display:none;">⚠ O período mudou. Clique em Verificar para recalcular.</div>
    <div id="sim-result"></div>
  </div>
</div>

</div><!-- /page-jogos -->

<footer>Dados: API oficial Caixa Econômica Federal • {gerado_em}</footer>

<script>
const DATA = {data_json};

const C = (id) => document.getElementById(id).getContext('2d');
const chartDefaults = {
  responsive: true,
  maintainAspectRatio: true,
  plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#64748b', font: { size: 9 } }, grid: { color: '#1e2130' } },
    y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e2130' } }
  }
};

const chartRegistry = {};
function criarChart(id, config) {
  if (chartRegistry[id]) chartRegistry[id].destroy();
  chartRegistry[id] = new Chart(C(id), config);
  return chartRegistry[id];
}

function animarContador(el, valorFinal, decimais) {
  decimais = decimais || 0;
  el.textContent = decimais ? (0).toFixed(decimais) : '0';
  const passos = 40;
  let passoAtual = 0;
  let concluido = false;
  function finalizar() {
    if (concluido) return;
    concluido = true;
    el.textContent = decimais ? valorFinal.toFixed(decimais) : String(Math.round(valorFinal));
  }
  function frame() {
    if (concluido) return;
    passoAtual++;
    const p = Math.min(1, passoAtual / passos);
    const eased = 1 - Math.pow(1 - p, 3);
    const atual = valorFinal * eased;
    el.textContent = decimais ? atual.toFixed(decimais) : String(Math.round(atual));
    if (passoAtual < passos) requestAnimationFrame(frame);
    else finalizar();
  }
  requestAnimationFrame(frame);
  setTimeout(finalizar, 1500);
}

// paleta âmbar por percentil (8 tons: cinza → âmbar → dourado) usada em
// numgrid, matriz de blocos, heatmap mensal e gráfico de frequência —
// substitui a interpolação linear de 2 pontos anterior
const HEAT_STOPS = [
  { bg: '#1a1a24', fg: '#404058' },
  { bg: '#2e2010', fg: '#d97706' },
  { bg: '#3a2812', fg: '#fbbf24' },
  { bg: '#483014', fg: '#fcd34d' },
  { bg: '#583c16', fg: '#fde68a' },
  { bg: '#6b4a18', fg: '#fef3c7' },
  { bg: '#c4973a', fg: '#fff8e8' },
  { bg: '#d4a830', fg: '#fff9e0' },
];
function stopFor(p) {
  if (p >= 0.90) return HEAT_STOPS[7];
  if (p >= 0.70) return HEAT_STOPS[6];
  if (p >= 0.55) return HEAT_STOPS[5];
  if (p >= 0.40) return HEAT_STOPS[4];
  if (p >= 0.25) return HEAT_STOPS[3];
  if (p >= 0.10) return HEAT_STOPS[2];
  if (p > 0)     return HEAT_STOPS[1];
  return HEAT_STOPS[0];
}
function hexToRgba(hex, alpha) {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0, 2), 16), g = parseInt(h.substring(2, 4), 16), b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
// mantém compatibilidade com os pontos de chamada que só usam o background
// (gráfico de frequência, matriz de coocorrência, heatmap mensal)
function corGradienteAmbar(t) {
  return stopFor(t).bg;
}

// ── módulos que reagem ao seletor de período ─────────────────────────────────

function renderKpisPeriodo(bundle) {
  const freq = bundle.frequencia;
  const sorted_freq = Object.entries(freq).map(([d,c])=>({d:+d,c})).sort((a,b)=>b.c-a.c);
  animarContador(document.getElementById('kpi-total'), bundle.meta.total);
  document.getElementById('kpi-top1').textContent = String(sorted_freq[0].d).padStart(2,'0');
  document.getElementById('kpi-top1-sub').textContent = sorted_freq[0].c + ' vezes (' + (sorted_freq[0].c/bundle.meta.total*100).toFixed(1) + '%)';
  document.getElementById('kpi-bot1').textContent = String(sorted_freq[sorted_freq.length-1].d).padStart(2,'0');
  document.getElementById('kpi-bot1-sub').textContent = sorted_freq[sorted_freq.length-1].c + ' vezes (' + (sorted_freq[sorted_freq.length-1].c/bundle.meta.total*100).toFixed(1) + '%)';
  const avgPares = bundle.pares_impares.reduce((a,b)=>a+b.pares,0)/bundle.pares_impares.length;
  animarContador(document.getElementById('kpi-pares'), avgPares, 1);
  animarContador(document.getElementById('kpi-soma'), bundle.somas.reduce((a,b)=>a+b,0)/bundle.somas.length, 0);
  const tamanhos = Object.keys(bundle.seq_dist_tamanho).map(Number);
  const maxSeq = tamanhos.length ? Math.max(...tamanhos) : 0;
  animarContador(document.getElementById('kpi-seq'), maxSeq, 0);
  const pctPares = (avgPares/20*100).toFixed(1);
  document.getElementById('pct-pares').textContent = pctPares + '%';
  document.getElementById('pct-impares').textContent = (100-pctPares).toFixed(1) + '%';
}

let numerosSelecionados = new Set();
let hotcoldJanelaAtual = 30;

function renderNumGrid(bundle) {
  const grid = document.getElementById('numgrid');
  grid.innerHTML = '';
  const freq = bundle.frequencia;
  const entradas = Object.entries(freq).map(([d, c]) => ({ d: +d, c }));
  const ordenado = [...entradas].sort((a, b) => a.c - b.c);
  const rank = new Map(ordenado.map((e, i) => [e.d, i / Math.max(1, ordenado.length - 1)]));
  for (let d = 0; d <= 99; d++) {  // universo 00-99 (zero-indexado) — NÃO alterar para 1-100
    const cnt = freq[d] || 0;
    const p = rank.get(d) ?? 0;
    const { bg, fg } = stopFor(p);
    const cell = document.createElement('div');
    cell.className = 'numgrid-cell' + (numerosSelecionados.has(d) ? ' selecionada' : '');
    cell.dataset.num = String(d);
    cell.style.background = bg;
    cell.style.color = fg;
    cell.textContent = String(d).padStart(2, '0');
    cell.title = `Dezena ${String(d).padStart(2,'0')}: ${cnt} vezes (${bundle.meta.total ? (cnt/bundle.meta.total*100).toFixed(1) : '0.0'}%)`;
    grid.appendChild(cell);
  }
}

function renderPI(bundle) {
  const total_p = bundle.pares_impares.reduce((a,b)=>a+b.pares,0);
  const total_i = bundle.pares_impares.reduce((a,b)=>a+b.impares,0);
  criarChart('chartPI', {
    type: 'doughnut',
    data: {
      labels: ['Pares','Ímpares'],
      datasets: [{ data: [total_p, total_i], backgroundColor: ['#d97706','#fbbf24'], borderWidth: 0 }]
    },
    options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { labels: { color: '#94a3b8' } } }, cutout: '65%' }
  });
}

// ── Frequência por dezena em 5 abas de 20 (mesmos blocos A-E) — um único
// gráfico de barras com 100 dezenas ficaria ilegível, então tabula por bloco
// (troca de aba só redesenha o mesmo canvas, sem recriar o DOM) ─────────────
function rangeArr(a, b) { const r = []; for (let i = a; i <= b; i++) r.push(i); return r; }
const FREQ_TAB_RANGES = [
  { nome: 'A', label: '01–20', dezenas: rangeArr(1, 20) },
  { nome: 'B', label: '21–40', dezenas: rangeArr(21, 40) },
  { nome: 'C', label: '41–60', dezenas: rangeArr(41, 60) },
  { nome: 'D', label: '61–80', dezenas: rangeArr(61, 80) },
  { nome: 'E', label: '81–00', dezenas: [...rangeArr(81, 99), 0] },
];
let freqTabAtual = 'A';

function renderChartFreqRange(bundle, range) {
  const freq = bundle.frequencia;
  const vals = Object.values(freq);
  const minV = Math.min(...vals), maxV = Math.max(...vals);
  const labels = [], data = [], colors = [];
  range.dezenas.forEach(d => {
    labels.push(String(d).padStart(2,'0'));
    const c = freq[d] || 0;
    data.push(c);
    const t = maxV > minV ? (c - minV) / (maxV - minV) : 0;
    colors.push(corGradienteAmbar(t));
  });
  criarChart('chartFreq', {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Vezes sorteada', data, backgroundColor: colors, borderRadius: 4 }] },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderChartFreqTabs(bundle) {
  const tabsEl = document.getElementById('freq-tabs');
  tabsEl.innerHTML = '';
  FREQ_TAB_RANGES.forEach(r => {
    const tab = document.createElement('button');
    tab.className = 'tab' + (r.nome === freqTabAtual ? ' active' : '');
    tab.textContent = 'Bloco ' + r.nome + ' (' + r.label + ')';
    tab.addEventListener('click', () => {
      freqTabAtual = r.nome;
      tabsEl.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      renderChartFreqRange(bundle, r);
    });
    tabsEl.appendChild(tab);
  });
  const atual = FREQ_TAB_RANGES.find(r => r.nome === freqTabAtual) || FREQ_TAB_RANGES[0];
  renderChartFreqRange(bundle, atual);
}

function renderChartAtraso(bundle) {
  const atr = Object.entries(bundle.atraso).map(([d,a])=>({d:+d,a})).sort((a,b)=>b.a-a.a);
  // limiares ~metade dos da Mega-Sena: cada sorteio cobre 20% do universo
  // (vs. 10% da Mega-Sena), então o atraso "esperado" também é ~metade
  const colors = atr.map(x => x.a >= 15 ? '#ef4444' : x.a >= 8 ? '#f59e0b' : '#10b981');
  criarChart('chartAtraso', {
    type: 'bar',
    data: {
      labels: atr.map(x => String(x.d).padStart(2,'0')),
      datasets: [{ label: 'Sorteios de atraso', data: atr.map(x=>x.a), backgroundColor: colors, borderRadius: 4 }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderChartFaixas(bundle) {
  const n = bundle.faixas.length;
  const media = { A: 0, B: 0, C: 0, D: 0, E: 0 };
  bundle.faixas.forEach(f => { media.A += f.A; media.B += f.B; media.C += f.C; media.D += f.D; media.E += f.E; });
  criarChart('chartFaixas', {
    type: 'bar',
    data: {
      labels: ['A (01-20)', 'B (21-40)', 'C (41-60)', 'D (61-80)', 'E (81-00)'],
      datasets: [{
        label: 'Média de dezenas por sorteio',
        data: [media.A/n, media.B/n, media.C/n, media.D/n, media.E/n].map(v=>+v.toFixed(2)),
        backgroundColor: ['#fde68a','#fbbf24','#f59e0b','#d97706','#b45309'], borderRadius: 4
      }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderChartSoma(bundle) {
  const somas = bundle.somas;
  const min = Math.min(...somas), max = Math.max(...somas);
  const nBuckets = 12;
  const tamanho = Math.max(1, Math.ceil((max - min + 1) / nBuckets));
  const buckets = {};
  somas.forEach(s => {
    const b = min + Math.floor((s - min) / tamanho) * tamanho;
    buckets[b] = (buckets[b] || 0) + 1;
  });
  const labels = Object.keys(buckets).map(Number).sort((a,b)=>a-b);
  criarChart('chartSoma', {
    type: 'bar',
    data: {
      labels: labels.map(b => `${b}-${b+tamanho-1}`),
      datasets: [{ label: 'Sorteios', data: labels.map(b => buckets[b]), backgroundColor: '#d97706', borderRadius: 4 }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderSeqTabs(bundle) {
  const tabsEl = document.getElementById('seq-tabs');
  const contentsEl = document.getElementById('seq-contents');
  tabsEl.innerHTML = '';
  contentsEl.innerHTML = '';
  const tops = bundle.seq_top_por_tamanho;
  const tamanhos = Object.keys(tops).map(Number).sort((a,b)=>a-b);

  tamanhos.forEach((tam, idx) => {
    const tab = document.createElement('button');
    tab.className = 'tab' + (idx === 0 ? ' active' : '');
    tab.textContent = tam + ' números';
    tab.dataset.tam = tam;
    tab.addEventListener('click', () => {
      tabsEl.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      contentsEl.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById('seq-' + tam).classList.add('active');
    });
    tabsEl.appendChild(tab);

    const div = document.createElement('div');
    div.className = 'tab-content' + (idx === 0 ? ' active' : '');
    div.id = 'seq-' + tam;

    const items = tops[tam] || [];
    if (!items.length) {
      div.innerHTML = '<p style="color:var(--muted)">Nenhuma sequência deste tamanho encontrada.</p>';
    } else {
      const maxCount = items[0].count;
      items.forEach(item => {
        const row = document.createElement('div');
        row.className = 'bar-row';
        const tags = item.seq.map(n => `<span class="seq-tag">${String(n).padStart(2,'0')}</span>`).join('');
        const w = Math.round(item.count / maxCount * 100);
        row.innerHTML = `
          <div style="min-width:160px">${tags}</div>
          <div class="bar" style="width:${w}%; max-width:200px;"></div>
          <div class="val">${item.count}×</div>`;
        div.appendChild(row);
      });
    }
    contentsEl.appendChild(div);
  });
}

function renderCoocList(bundle) {
  const list = document.getElementById('cooc-list');
  list.innerHTML = '';
  const items = bundle.coocorrencia;
  if (!items.length) { list.innerHTML = '<p style="color:var(--muted)">Sem dados suficientes.</p>'; return; }
  const maxC = Math.max(...items.map(x => x[1]));
  items.forEach(([pair, cnt]) => {
    const [a, b] = pair;
    const w = Math.round(cnt / maxC * 100);
    const row = document.createElement('div');
    row.className = 'bar-row';
    row.innerHTML = `
      <div style="min-width:70px"><span class="seq-tag">${String(a).padStart(2,'0')}</span><span class="seq-tag">${String(b).padStart(2,'0')}</span></div>
      <div class="bar" style="width:${w}%; max-width:140px;"></div>
      <div class="val">${cnt}×</div>`;
    list.appendChild(row);
  });
}

function renderAntiCorr(bundle) {
  const list = document.getElementById('anticorr-list');
  list.innerHTML = '';
  const items = bundle.anticorrelacao;
  if (!items.length) { list.innerHTML = '<p style="color:var(--muted)">Sem dados suficientes neste período.</p>'; return; }
  const maxC = Math.max(...items.map(x => x[1]));
  items.forEach(([pair, cnt]) => {
    const [a, b] = pair;
    const w = Math.max(4, Math.round(cnt / maxC * 100));
    const row = document.createElement('div');
    row.className = 'bar-row';
    row.innerHTML = `
      <div style="min-width:70px"><span class="seq-tag">${String(a).padStart(2,'0')}</span><span class="seq-tag">${String(b).padStart(2,'0')}</span></div>
      <div class="bar" style="width:${w}%; max-width:140px; background:#06b6d4;"></div>
      <div class="val">${cnt}×</div>`;
    list.appendChild(row);
  });
}

function renderChartTend(bundle) {
  const dados = bundle.tendencia.slice().sort((a,b) => b.delta - a.delta);
  const colors = dados.map(x => x.delta >= 0 ? '#10b981' : '#ef4444');
  criarChart('chartTend', {
    type: 'bar',
    data: {
      labels: dados.map(x => String(x.d).padStart(2,'0')),
      datasets: [{ label: 'Δ recente vs. total (pp)', data: dados.map(x => x.delta), backgroundColor: colors, borderRadius: 4 }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderCicloMedio(bundle) {
  const list = document.getElementById('ciclo-list');
  list.innerHTML = '';
  const atrasoMap = bundle.atraso;
  const items = Object.entries(bundle.ciclo_medio)
    .map(([d, v]) => ({ d: +d, ciclo: v.ciclo, aparicoes: v.aparicoes, atraso: atrasoMap[d] }))
    .filter(x => x.ciclo !== null)
    .sort((a, b) => a.ciclo - b.ciclo);
  if (!items.length) {
    list.innerHTML = '<p style="color:var(--muted)">Sem dados suficientes neste período (nenhuma dezena repetiu).</p>';
    return;
  }
  const maxCiclo = Math.max(...items.map(x => x.ciclo));
  const table = document.createElement('table');
  table.innerHTML = `<thead><tr><th>Dezena</th><th>Ciclo médio</th><th>Atraso atual</th><th>Status</th></tr></thead>`;
  const tbody = document.createElement('tbody');
  items.forEach(item => {
    const w = Math.round(item.ciclo / maxCiclo * 100);
    const alem = item.atraso >= item.ciclo;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><span class="seq-tag">${String(item.d).padStart(2,'0')}</span></td>
      <td><div class="bar-row" style="margin:0"><div class="bar" style="width:${w}%; max-width:120px;"></div><div class="val">${item.ciclo}</div></div></td>
      <td>${item.atraso}</td>
      <td><span class="status-tag ${alem ? 'alem' : 'dentro'}">${alem ? 'além do normal' : 'dentro do ciclo'}</span></td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  list.appendChild(table);
}

function renderRepeticao(bundle) {
  const rep = bundle.repeticao_anterior;
  const media = rep.length ? rep.reduce((a,b)=>a+b,0) / rep.length : 0;
  document.getElementById('rep-media').textContent = media.toFixed(1);
  document.getElementById('rep-min').textContent = rep.length ? Math.min(...rep) : 0;
  document.getElementById('rep-max').textContent = rep.length ? Math.max(...rep) : 0;
  animarContador(document.getElementById('kpi-repeticao'), media, 1);
  document.getElementById('kpi-repeticao-sub').textContent = 'dezenas repetidas em média';

  const dist = new Array(21).fill(0);
  rep.forEach(v => { if (v <= 20) dist[v]++; });
  criarChart('chartRepeticao', {
    type: 'bar',
    data: {
      labels: dist.map((_,i) => String(i)),
      datasets: [{ label: 'Sorteios', data: dist, backgroundColor: '#d97706', borderRadius: 4 }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

// ── Números quentes e frios — a janela de recência (15/30/50) e a linha de
// base de comparação usam só os sorteios do bundle ativo (bundle.sorteios_raw
// já vem pronto — precomputado por período no Python ou mesclado em JS pra
// múltiplos anos selecionados). ───────────────────────────────────────────────
function renderHotCold(bundle, janela) {
  hotcoldJanelaAtual = janela;
  const grid = document.getElementById('hotcold-grid');
  grid.innerHTML = '';
  const sorteiosPeriodo = bundle.sorteios_raw || [];
  const totalPeriodo = sorteiosPeriodo.length;
  const janelaEfetiva = Math.min(janela, totalPeriodo);
  const recentes = sorteiosPeriodo.slice(-janelaEfetiva);
  const freqRec = {};
  for (let d = 0; d <= 99; d++) freqRec[d] = 0;
  recentes.forEach(s => s.forEach(d => freqRec[d]++));

  for (let d = 0; d <= 99; d++) {
    const pctTotal = totalPeriodo ? (bundle.frequencia[d] || 0) / totalPeriodo * 100 : 0;
    const pctRec = janelaEfetiva ? freqRec[d] / janelaEfetiva * 100 : 0;
    const delta = pctTotal > 0 ? (pctRec - pctTotal) / pctTotal * 100 : 0;
    let status = 'normal', emoji = '~';
    if (delta >= 20) { status = 'quente'; emoji = '🔥'; }
    else if (delta <= -20) { status = 'frio'; emoji = '❄️'; }

    const card = document.createElement('div');
    card.className = 'hotcold-card ' + status;
    card.innerHTML = `
      <div class="num">${String(d).padStart(2,'0')}</div>
      <div class="status">${emoji}</div>
      <div class="pct">${pctRec.toFixed(0)}% (Δ${delta >= 0 ? '+' : ''}${delta.toFixed(0)}%)</div>`;
    grid.appendChild(card);
  }
}
{
  const btns = document.querySelectorAll('#hotcold-btns .tab');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      btns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderHotCold(resolverBundlePeriodo(periodoAtualId), +btn.dataset.janela);
    });
  });
}

function renderBlocos(bundle) {
  const b = bundle.blocos;
  const nomes = ['A','B','C','D','E'];
  const faixaLabel = { A: '01 a 20', B: '21 a 40', C: '41 a 60', D: '61 a 80', E: '81 a 00' };
  const total = bundle.meta.total;

  const grid = document.getElementById('blocos-ranking-grid');
  grid.innerHTML = '';
  const campeoes = [];

  nomes.forEach(nome => {
    const ranking = Object.entries(b.freq_individual[nome])
      .map(([num, cnt]) => ({ num: +num, cnt }))
      .sort((x, y) => y.cnt - x.cnt);

    const maxCnt = ranking[0].cnt;
    const top = ranking[0];
    const bottom = ranking[ranking.length - 1];
    campeoes.push({ nome, top, bottom });

    const letra = nome.toLowerCase();
    document.getElementById('kpi-bloco-' + letra).textContent = String(top.num).padStart(2,'0');
    document.getElementById('kpi-bloco-' + letra + '-sub').textContent =
      `${top.cnt}x (${total ? (top.cnt/total*100).toFixed(1) : '0.0'}%)`;

    const card = document.createElement('div');
    card.className = 'bloco-card';
    const titulo = document.createElement('h3');
    titulo.textContent = `Bloco ${nome} — ${faixaLabel[nome]}`;
    card.appendChild(titulo);

    ranking.forEach((item, idx) => {
      const isTop = idx === 0;
      const isBottom = idx === ranking.length - 1 && ranking.length > 1;
      const pct = total ? (item.cnt / total * 100).toFixed(1) : '0.0';
      const w = maxCnt > 0 ? Math.round(item.cnt / maxCnt * 100) : 0;
      const row = document.createElement('div');
      row.className = 'bloco-rank-row' + (isTop ? ' top' : isBottom ? ' bottom' : '');
      const medalha = isTop ? '🥇' : isBottom ? '🔻' : '';
      row.innerHTML = `
        <div class="medalha">${medalha}</div>
        <div class="bloco-num ${isTop ? 'top' : isBottom ? 'bottom' : ''}">${String(item.num).padStart(2,'0')}</div>
        <div class="bloco-bar-wrap"><div class="bloco-bar" style="width:${w}%"></div></div>
        <div class="contagem">${item.cnt} · ${pct}%</div>`;
      card.appendChild(row);
    });

    grid.appendChild(card);
  });

  {
    const el = document.getElementById('blocos-campeoes');
    el.innerHTML = '';
    const table = document.createElement('table');
    table.innerHTML = `<thead><tr><th>Bloco</th><th>Mais sorteado</th><th>Menos sorteado</th></tr></thead>`;
    const tbody = document.createElement('tbody');
    campeoes.forEach(c => {
      const pctTop = total ? (c.top.cnt/total*100).toFixed(1) : '0.0';
      const pctBottom = total ? (c.bottom.cnt/total*100).toFixed(1) : '0.0';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${c.nome} (${faixaLabel[c.nome]})</td>
        <td><span class="seq-tag">${String(c.top.num).padStart(2,'0')}</span> — ${c.top.cnt}x (${pctTop}%)</td>
        <td><span class="seq-tag">${String(c.bottom.num).padStart(2,'0')}</span> — ${c.bottom.cnt}x (${pctBottom}%)</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    el.appendChild(table);
  }

  {
    const el = document.getElementById('blocos-combinacoes');
    el.innerHTML = '';
    if (!b.combinacoes.length) {
      el.innerHTML = '<p style="color:var(--muted)">Sem dados suficientes neste período.</p>';
    } else {
      const table = document.createElement('table');
      table.innerHTML = `<thead><tr><th>#</th><th>A-B-C-D-E</th><th>Vezes</th><th>%</th></tr></thead>`;
      const tbody = document.createElement('tbody');
      b.combinacoes.forEach((item, i) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td style="color:var(--muted)">${i+1}</td><td><span class="seq-tag">${item.combinacao}</span></td><td>${item.count}</td><td>${item.pct}%</td>`;
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      el.appendChild(table);
    }
  }

  {
    const wrap = document.getElementById('blocos-coocorrencia');
    wrap.innerHTML = '';
    const vals = b.coocorrencia.flat();
    const minV = Math.min(...vals), maxV = Math.max(...vals);
    wrap.appendChild(document.createElement('div'));
    nomes.forEach(n => {
      const h = document.createElement('div');
      h.className = 'colhead';
      h.textContent = n;
      wrap.appendChild(h);
    });
    nomes.forEach((nomeLinha, i) => {
      const h = document.createElement('div');
      h.className = 'rowhead';
      h.textContent = nomeLinha;
      wrap.appendChild(h);
      nomes.forEach((nomeCol, j) => {
        const cnt = b.coocorrencia[i][j];
        const t = maxV > minV ? (cnt - minV) / (maxV - minV) : 0;
        const cell = document.createElement('div');
        cell.className = 'heatcell';
        cell.style.background = corGradienteAmbar(t);
        cell.style.color = t > 0.4 ? '#fff' : '#ccc';
        cell.style.fontSize = '12px';
        cell.textContent = cnt;
        cell.title = i === j
          ? `Bloco ${nomeLinha} sozinho com ≥4 dezenas: ${cnt} vezes`
          : `Blocos ${nomeLinha} e ${nomeCol} juntos com ≥4 dezenas cada: ${cnt} vezes`;
        wrap.appendChild(cell);
      });
    });
  }
}

function renderFinanceiro(bundle) {
  const el = document.getElementById('financeiro-resumo');
  el.innerHTML = '';
  const f = bundle.financeiro;
  if (!f || !f.total_sorteios) {
    el.innerHTML = '<p style="color:var(--muted)">Sem dados financeiros neste período.</p>';
    return;
  }
  const itens = [
    { label: 'Total de prêmios pagos', valor: formatarMoeda(f.total_premios_pagos) },
    { label: 'Prêmio médio (20 acertos)', valor: f.media_premio_faixa1 != null ? formatarMoeda(f.media_premio_faixa1) : '—' },
    {
      label: 'Maior prêmio pago',
      valor: f.maior_premio ? formatarMoeda(f.maior_premio.valor) : '—',
      sub: f.maior_premio ? `Concurso ${f.maior_premio.concurso} — ${f.maior_premio.data}` : '',
    },
    {
      label: 'Menor prêmio pago',
      valor: f.menor_premio ? formatarMoeda(f.menor_premio.valor) : '—',
      sub: f.menor_premio ? `Concurso ${f.menor_premio.concurso} — ${f.menor_premio.data}` : '',
    },
    { label: 'Sorteios acumulados', valor: f.total_acumulados, sub: `${f.pct_acumulados}% do período` },
  ];
  itens.forEach(item => {
    const div = document.createElement('div');
    div.className = 'fin-item';
    div.innerHTML = `<div class="label">${item.label}</div><div class="value">${item.valor}</div>` +
      (item.sub ? `<div class="sub">${item.sub}</div>` : '');
    el.appendChild(div);
  });
}

function renderSurpresa(bundle) {
  const el = document.getElementById('surpresa-resumo');
  el.innerHTML = '';
  const fs = bundle.faixa_surpresa;
  if (!fs || !fs.total_sorteios) {
    el.innerHTML = '<p style="color:var(--muted)">Sem dados neste período.</p>';
    return;
  }
  const itens = [
    { label: 'Sorteios com surpresa premiada', valor: fs.qtd_sorteios_surpresa, sub: `${fs.pct_sorteios_surpresa}% do período` },
    { label: 'Média de apostas ganhadoras', valor: fs.media_ganhadores_surpresa != null ? fs.media_ganhadores_surpresa.toLocaleString('pt-BR') : '—' },
    {
      label: 'Surpresa mais recente',
      valor: fs.mais_recente ? `Concurso ${fs.mais_recente.concurso}` : '—',
      sub: fs.mais_recente ? `${fs.mais_recente.data} — ${fs.mais_recente.ganhadores} aposta(s)` : '',
    },
    { label: 'Maior intervalo sem surpresa', valor: `${fs.maior_gap_sem_surpresa} sorteios` },
  ];
  itens.forEach(item => {
    const div = document.createElement('div');
    div.className = 'fin-item';
    div.innerHTML = `<div class="label">${item.label}</div><div class="value">${item.valor}</div>` +
      (item.sub ? `<div class="sub">${item.sub}</div>` : '');
    el.appendChild(div);
  });
}

function formatarMoeda(v) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatarPct(v) {
  return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
}

function renderBannerPeriodo(periodoId, bundle) {
  const banner = document.getElementById('periodo-banner');
  if (periodoId === '__todos__') {
    banner.style.display = 'none';
    return;
  }
  const info = (DATA.periodos_disponiveis || []).find(p => p.id === periodoId);
  const label = info ? info.label : periodoId;
  const inicio = bundle.meta.inicio || '—';
  const fim = bundle.meta.fim || '—';
  banner.textContent = `⚡ Exibindo: ${label} · ${bundle.meta.total} sorteios · ${inicio}–${fim}`;
  banner.style.display = 'block';
}

// ── Meus Jogos — validador financeiro (aba dedicada). O cálculo financeiro
// (ganho/gasto/saldo/ROI, histórico de sorteios em que pontuou, evolução do
// saldo) já vem pronto em bundle.jogos (Python) ou calcJogosLotomaniaJS (JS,
// mesclagem multi-ano) — aqui só ordena e desenha. ───────────────────────────

const ESTADO_ORDENACAO_JOGOS_LOTOMANIA = { coluna: 'saldo', direcao: -1 };
const COLUNAS_JOGOS_LOTOMANIA = [
  { key: 'idx', label: '#', ordenavel: false },
  { key: 'nome', label: 'Jogo', ordenavel: true },
  { key: 'p20', label: '20pts', ordenavel: true },
  { key: 'p19', label: '19pts', ordenavel: true },
  { key: 'p18', label: '18pts', ordenavel: true },
  { key: 'p17', label: '17pts', ordenavel: true },
  { key: 'p16', label: '16pts', ordenavel: true },
  { key: 'p15', label: '15pts', ordenavel: true },
  { key: 'surpresa', label: 'Surpresa', ordenavel: true },
  { key: 'total_premiado', label: 'Total prem.', ordenavel: true },
  { key: 'gasto', label: 'Gasto', ordenavel: true },
  { key: 'ganho', label: 'Ganho', ordenavel: true },
  { key: 'saldo', label: 'Saldo', ordenavel: true },
  { key: 'roi', label: 'ROI', ordenavel: true },
  { key: 'melhor', label: 'Melhor faixa', ordenavel: false },
];

function valorOrdenacaoJogoLotomania(jogo, coluna) {
  switch (coluna) {
    case 'nome': return jogo.nome;
    case 'p20': return jogo.contagem[20];
    case 'p19': return jogo.contagem[19];
    case 'p18': return jogo.contagem[18];
    case 'p17': return jogo.contagem[17];
    case 'p16': return jogo.contagem[16];
    case 'p15': return jogo.contagem[15];
    case 'surpresa': return jogo.contagem[0];
    default: return jogo[coluna];
  }
}

function labelPeriodoParaJogos(periodoId) {
  if (periodoId === '__todos__') return 'Todos os sorteios';
  if (periodoId.startsWith('MULTI:')) return periodoId.slice(6).split(',').join(' + ');
  const info = (DATA.periodos_disponiveis || []).find(p => p.id === periodoId);
  return info ? info.label : periodoId;
}

function renderJogosResumoLotomania(jogos, nSorteios, periodoLabel) {
  const el = document.getElementById('jogos-resumo');
  const nJogos = jogos.length;
  const custoPorSorteio = +(CUSTO_LOTOMANIA_JS * nJogos).toFixed(2);
  const gastoTotal = jogos.reduce((a, j) => a + j.gasto, 0);
  const ganhoTotal = jogos.reduce((a, j) => a + j.ganho, 0);
  const saldoTotal = +(ganhoTotal - gastoTotal).toFixed(2);
  const roiTotal = gastoTotal ? (saldoTotal / gastoTotal * 100) : 0;
  el.innerHTML = `
    <div class="jogos-resumo-titulo">${nJogos} jogo${nJogos === 1 ? '' : 's'} · ${formatarMoeda(custoPorSorteio)} por sorteio · Período ativo: ${periodoLabel}</div>
    <div class="mini-stats">
      <div>Sorteios no período<b>${nSorteios}</b></div>
      <div>Total apostado<b>${formatarMoeda(gastoTotal)}</b></div>
      <div>Total ganho<b>${formatarMoeda(ganhoTotal)}</b></div>
      <div>Saldo<b class="${saldoTotal >= 0 ? 'money-pos' : 'money-neg'}">${formatarMoeda(saldoTotal)}</b></div>
      <div>ROI<b class="${roiTotal >= 0 ? 'money-pos' : 'money-neg'}">${formatarPct(roiTotal)}</b></div>
    </div>`;
}

function construirDetalheJogoLotomania(container, jogo, canvasIdBase) {
  const grid = document.createElement('div');
  grid.className = 'jogos-detail-grid';

  const graficosDiv = document.createElement('div');
  graficosDiv.innerHTML = `
    <div class="jogos-detail-titulo">Distribuição de acertos (Surpresa/15/16/17/18/19/20)</div>
    <canvas id="${canvasIdBase}-dist"></canvas>
    <div class="jogos-detail-titulo" style="margin-top:16px">Evolução do saldo acumulado</div>
    <canvas id="${canvasIdBase}-evol"></canvas>`;

  const listaDiv = document.createElement('div');
  listaDiv.style.cssText = 'max-height:280px; overflow-y:auto;';
  const tituloLista = document.createElement('div');
  tituloLista.className = 'jogos-detail-titulo';
  tituloLista.textContent = `Sorteios premiados (${jogo.historico.length})`;
  listaDiv.appendChild(tituloLista);
  if (!jogo.historico.length) {
    const p = document.createElement('p');
    p.style.cssText = 'color:var(--muted); font-size:12px;';
    p.textContent = 'Nenhum sorteio premiado neste período.';
    listaDiv.appendChild(p);
  } else {
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th>Concurso</th><th>Data</th><th>Faixa</th><th>Prêmio</th></tr></thead>';
    const tbody = document.createElement('tbody');
    [...jogo.historico].reverse().forEach(h => {
      const tr = document.createElement('tr');
      const surpresa = h.acertos === 0;
      if (surpresa) tr.className = 'jogos-hist-surpresa';
      const faixaTxt = surpresa ? '0️⃣ Surpresa' : `${h.acertos} pts`;
      tr.innerHTML = `<td>${h.concurso}</td><td>${h.data}</td><td>${faixaTxt}</td><td>${formatarMoeda(h.premio)}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    listaDiv.appendChild(table);
  }

  grid.appendChild(graficosDiv);
  grid.appendChild(listaDiv);
  container.appendChild(grid);

  // canvas precisa estar no DOM com layout resolvido antes do Chart.js medir
  requestAnimationFrame(() => {
    criarChart(`${canvasIdBase}-dist`, {
      type: 'bar',
      data: {
        labels: ['Surpresa (0)', '15', '16', '17', '18', '19', '20'],
        datasets: [{ data: [0, 15, 16, 17, 18, 19, 20].map(p => jogo.contagem[p]), backgroundColor: '#d97706', borderRadius: 4 }],
      },
      options: { ...chartDefaults, plugins: { legend: { display: false } } },
    });
    criarChart(`${canvasIdBase}-evol`, {
      type: 'line',
      data: {
        labels: jogo.saldo_evolucao.map((_, i) => i + 1),
        datasets: [{
          data: jogo.saldo_evolucao, borderColor: '#fbbf24', backgroundColor: 'rgba(217,119,6,.15)',
          fill: true, pointRadius: 0, borderWidth: 2, tension: .15,
        }],
      },
      options: { ...chartDefaults, plugins: { legend: { display: false } } },
    });
  });
}

function renderJogosTabelaLotomania(jogos) {
  const wrap = document.getElementById('jogos-tabela-wrap');
  wrap.innerHTML = '';

  const estado = ESTADO_ORDENACAO_JOGOS_LOTOMANIA;
  const ordenados = [...jogos].sort((a, b) => {
    const va = valorOrdenacaoJogoLotomania(a, estado.coluna), vb = valorOrdenacaoJogoLotomania(b, estado.coluna);
    if (typeof va === 'string') return va.localeCompare(vb) * estado.direcao;
    return (va - vb) * estado.direcao;
  });

  const melhorRoi = jogos.reduce((m, j) => (j.roi > m.roi ? j : m), jogos[0]);
  const maisPremiado = jogos.reduce((m, j) => (j.total_premiado > m.total_premiado ? j : m), jogos[0]);
  const maisSurpresa = jogos.reduce((m, j) => (j.contagem[0] > m.contagem[0] ? j : m), jogos[0]);

  const table = document.createElement('table');
  table.className = 'jogos-tabela';
  const thead = document.createElement('thead');
  const trHead = document.createElement('tr');
  COLUNAS_JOGOS_LOTOMANIA.forEach(col => {
    const th = document.createElement('th');
    if (col.ordenavel) {
      th.dataset.col = col.key;
      const seta = estado.coluna === col.key ? (estado.direcao === 1 ? '▲' : '▼') : '';
      th.innerHTML = `${col.label} <span class="sort-arrow">${seta}</span>`;
      th.addEventListener('click', () => {
        if (estado.coluna === col.key) estado.direcao *= -1;
        else { estado.coluna = col.key; estado.direcao = col.key === 'nome' ? 1 : -1; }
        renderJogosTabelaLotomania(jogos);
      });
    } else {
      th.textContent = col.label;
    }
    trHead.appendChild(th);
  });
  thead.appendChild(trHead);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  ordenados.forEach((jogo, i) => {
    const tr = document.createElement('tr');
    tr.className = 'jogos-row ' + (jogo.saldo >= 0 ? 'saldo-pos' : 'saldo-neg');
    const melhorTxt = jogo.melhor.concurso
      ? `${jogo.melhor.acertos === 0 ? 'Surpresa (0 pts)' : jogo.melhor.acertos + ' pts'} (c.${jogo.melhor.concurso})`
      : '—';
    let nomeTxt = jogo.nome;
    if (jogo === melhorRoi) nomeTxt += ' <span title="Melhor ROI">🏆</span>';
    if (jogo === maisPremiado) nomeTxt += ' <span title="Mais sorteios premiados">🥇</span>';
    if (jogo === maisSurpresa && jogo.contagem[0] > 0) {
      nomeTxt += ' <span class="jogos-badge-surpresa" title="Mais faixas Surpresa (0 acertos) — a faixa mais divertida da Lotomania">🎯</span>';
    }
    tr.innerHTML = `
      <td style="color:var(--muted)">${i + 1}</td>
      <td>${nomeTxt}</td>
      <td>${jogo.contagem[20]}</td>
      <td>${jogo.contagem[19]}</td>
      <td>${jogo.contagem[18]}</td>
      <td>${jogo.contagem[17]}</td>
      <td>${jogo.contagem[16]}</td>
      <td>${jogo.contagem[15]}</td>
      <td>${jogo.contagem[0]}</td>
      <td>${jogo.total_premiado}</td>
      <td>${formatarMoeda(jogo.gasto)}</td>
      <td>${formatarMoeda(jogo.ganho)}</td>
      <td class="${jogo.saldo >= 0 ? 'money-pos' : 'money-neg'}">${formatarMoeda(jogo.saldo)}</td>
      <td class="${jogo.roi >= 0 ? 'money-pos' : 'money-neg'}">${formatarPct(jogo.roi)}</td>
      <td>${melhorTxt}</td>`;
    tbody.appendChild(tr);

    const trDetail = document.createElement('tr');
    trDetail.className = 'jogos-detail-row';
    const tdDetail = document.createElement('td');
    tdDetail.colSpan = COLUNAS_JOGOS_LOTOMANIA.length;
    const inner = document.createElement('div');
    inner.className = 'jogos-detail-inner';
    tdDetail.appendChild(inner);
    trDetail.appendChild(tdDetail);
    tbody.appendChild(trDetail);

    let construido = false;
    tr.addEventListener('click', () => {
      const aberto = inner.classList.contains('aberto');
      if (!aberto && !construido) {
        construirDetalheJogoLotomania(inner, jogo, 'jogoloto-' + i);
        construido = true;
      }
      inner.classList.toggle('aberto', !aberto);
    });
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
}

function renderJogos(bundle, periodoId) {
  const jogos = bundle.jogos;
  const resumoEl = document.getElementById('jogos-resumo');
  const tabelaWrapEl = document.getElementById('jogos-tabela-wrap');
  if (!jogos || !jogos.length) {
    resumoEl.innerHTML = '<p style="color:var(--muted)">Nenhum jogo configurado.</p>';
    tabelaWrapEl.innerHTML = '';
    return;
  }
  renderJogosResumoLotomania(jogos, bundle.meta.total, labelPeriodoParaJogos(periodoId));
  renderJogosTabelaLotomania(jogos);
  marcarSimuladorLotomaniaDesatualizado();
}

// avisa o simulador ad hoc (números digitados livremente) que o resultado
// mostrado não corresponde mais ao período ativo — ele só recalcula sob
// clique em "Verificar", nunca sozinho, então sem isso o resultado ficaria
// visualmente correto mas silenciosamente desatualizado após trocar período.
function marcarSimuladorLotomaniaDesatualizado() {
  const resultEl = document.getElementById('sim-result');
  const avisoEl = document.getElementById('sim-periodo-aviso');
  if (resultEl && avisoEl && resultEl.children.length > 0) {
    avisoEl.style.display = 'block';
  }
}

function renderPeriodoCompleto(bundle) {
  renderKpisPeriodo(bundle);
  renderPI(bundle);
  renderChartFreqTabs(bundle);
  renderChartAtraso(bundle);
  renderChartFaixas(bundle);
  renderChartSoma(bundle);
  renderSeqTabs(bundle);
  renderCoocList(bundle);
  renderAntiCorr(bundle);
  renderChartTend(bundle);
  renderCicloMedio(bundle);
  renderRepeticao(bundle);
  renderHotCold(bundle, hotcoldJanelaAtual);
  renderBlocos(bundle);
}

// ── grade interativa 10×10 — filtro client-side por combinação de dezenas ───
// (mesma arquitetura da Mega-Sena/Lotofácil: recalcula em JS os módulos
// period-aware sobre o subconjunto de sorteios que contêm TODAS as dezenas
// selecionadas, porque não dá pra pré-computar todas as combinações possíveis
// no servidor. blocos, financeiro e faixa_surpresa continuam mostrando o
// período ativo, não o subconjunto filtrado — mesma exceção documentada nas
// outras duas loterias.)

function calcFrequenciaJS(sorteios) {
  const freq = {};
  for (let d = 0; d <= 99; d++) freq[d] = 0;
  sorteios.forEach(s => s.forEach(d => freq[d]++));
  return freq;
}
function calcAtrasoJS(sorteios) {
  const n = sorteios.length, ultimo = {};
  sorteios.forEach((s, i) => s.forEach(d => { ultimo[d] = i; }));
  const atraso = {};
  for (let d = 0; d <= 99; d++) atraso[d] = (d in ultimo) ? (n - 1 - ultimo[d]) : n;
  return atraso;
}
function calcParesImparesJS(sorteios) {
  return sorteios.map(s => { const p = s.filter(d => d % 2 === 0).length; return { pares: p, impares: 20 - p }; });
}
function calcFaixasJS(sorteios) {
  return sorteios.map(s => ({
    A: s.filter(d => d >= 1 && d <= 20).length,
    B: s.filter(d => d >= 21 && d <= 40).length,
    C: s.filter(d => d >= 41 && d <= 60).length,
    D: s.filter(d => d >= 61 && d <= 80).length,
    E: s.filter(d => d === 0 || (d >= 81 && d <= 99)).length,
  }));
}
function calcSomaJS(sorteios) { return sorteios.map(s => s.reduce((a, b) => a + b, 0)); }
function calcSequenciasJS(sorteios) {
  const distTamanho = {}, contadorPorTamanho = {};
  sorteios.forEach(s => {
    const ordenado = [...s].sort((a, b) => a - b);
    let atual = [ordenado[0]];
    const runs = [];
    for (let i = 1; i < ordenado.length; i++) {
      if (ordenado[i] === atual[atual.length - 1] + 1) atual.push(ordenado[i]);
      else { if (atual.length >= 2) runs.push([...atual]); atual = [ordenado[i]]; }
    }
    if (atual.length >= 2) runs.push([...atual]);
    runs.forEach(r => {
      distTamanho[r.length] = (distTamanho[r.length] || 0) + 1;
      const key = r.join(',');
      contadorPorTamanho[r.length] = contadorPorTamanho[r.length] || new Map();
      contadorPorTamanho[r.length].set(key, (contadorPorTamanho[r.length].get(key) || 0) + 1);
    });
  });
  const topPorTamanho = {};
  Object.keys(contadorPorTamanho).forEach(tam => {
    if (+tam > 4) return; // abas só mostram tamanho 2, 3 e 4
    const arr = [...contadorPorTamanho[tam].entries()].map(([k, c]) => ({ seq: k.split(',').map(Number), count: c }));
    arr.sort((a, b) => b.count - a.count);
    topPorTamanho[tam] = arr.slice(0, 10);
  });
  return { distTamanho, topPorTamanho };
}
function calcCoocorrenciaJS(sorteios, topN) {
  const cont = new Map();
  sorteios.forEach(s => {
    const ordenado = [...s].sort((a, b) => a - b);
    for (let i = 0; i < ordenado.length; i++) for (let j = i + 1; j < ordenado.length; j++) {
      const key = ordenado[i] + ',' + ordenado[j];
      cont.set(key, (cont.get(key) || 0) + 1);
    }
  });
  const arr = [...cont.entries()].map(([k, c]) => ({ pair: k.split(',').map(Number), count: c }));
  arr.sort((a, b) => b.count - a.count);
  return arr.slice(0, topN || 20).map(x => [x.pair, x.count]);
}
function calcAntiCorrelacaoJS(sorteios, bottomN) {
  const cont = new Map();
  sorteios.forEach(s => {
    const ordenado = [...s].sort((a, b) => a - b);
    for (let i = 0; i < ordenado.length; i++) for (let j = i + 1; j < ordenado.length; j++) {
      const key = ordenado[i] + ',' + ordenado[j];
      cont.set(key, (cont.get(key) || 0) + 1);
    }
  });
  const arr = [...cont.entries()].map(([k, c]) => [k.split(',').map(Number), c]);
  arr.sort((a, b) => a[1] - b[1]);
  return arr.slice(0, bottomN || 15);
}
function calcTendenciaJS(sorteios) {
  const n = sorteios.length;
  const janela = Math.max(1, Math.min(50, n));
  const freqTotal = calcFrequenciaJS(sorteios);
  const freqRecente = calcFrequenciaJS(sorteios.slice(-janela));
  const dados = [];
  for (let d = 0; d <= 99; d++) {
    const ft = n ? +(freqTotal[d] / n * 100).toFixed(1) : 0;
    const fr = +(freqRecente[d] / janela * 100).toFixed(1);
    dados.push({ d, total: ft, recente: fr, delta: +(fr - ft).toFixed(1) });
  }
  return dados;
}
function calcCicloMedioJS(sorteios) {
  const indices = {};
  sorteios.forEach((s, i) => s.forEach(d => { (indices[d] = indices[d] || []).push(i); }));
  const resultado = {};
  for (let d = 0; d <= 99; d++) {
    const idx = indices[d] || [];
    const intervalos = [];
    for (let k = 1; k < idx.length; k++) intervalos.push(idx[k] - idx[k - 1]);
    resultado[d] = {
      ciclo: intervalos.length ? +(intervalos.reduce((a, b) => a + b, 0) / intervalos.length).toFixed(1) : null,
      aparicoes: idx.length,
    };
  }
  return resultado;
}
function calcRepeticaoAnteriorJS(sorteios) {
  const rep = [];
  for (let i = 1; i < sorteios.length; i++) {
    const anterior = new Set(sorteios[i - 1]);
    rep.push(sorteios[i].filter(d => anterior.has(d)).length);
  }
  return rep;
}

// ── funções de cálculo usadas na mesclagem client-side de múltiplos anos —
// espelham calc_blocos_bundle/calc_financeiro/calc_faixa_surpresa do Python,
// porque pré-computar todas as combinações possíveis de anos no servidor
// seria inviável (2^N combinações) ───────────────────────────────────────────

function calcBlocosJS(sorteios) {
  const blocoDe = d => d === 0 ? 4 : Math.floor((d - 1) / 20);
  const nomes = ['A', 'B', 'C', 'D', 'E'];
  const dezenasPorBloco = [rangeArr(1, 20), rangeArr(21, 40), rangeArr(41, 60), rangeArr(61, 80), [...rangeArr(81, 99), 0]];
  const freqIndividual = {};
  nomes.forEach((nome, i) => {
    freqIndividual[nome] = {};
    dezenasPorBloco[i].forEach(d => { freqIndividual[nome][d] = 0; });
  });
  sorteios.forEach(s => s.forEach(d => { freqIndividual[nomes[blocoDe(d)]][d]++; }));

  function contagemBlocos(s) {
    const c = [0, 0, 0, 0, 0];
    s.forEach(d => c[blocoDe(d)]++);
    return c;
  }
  const n = sorteios.length;
  const comboCont = new Map();
  sorteios.forEach(s => {
    const key = contagemBlocos(s).join('-');
    comboCont.set(key, (comboCont.get(key) || 0) + 1);
  });
  const combinacoes = [...comboCont.entries()]
    .map(([combinacao, count]) => ({ combinacao, count, pct: n ? +(count / n * 100).toFixed(1) : 0 }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 15);

  const matriz = [0, 1, 2, 3, 4].map(() => [0, 0, 0, 0, 0]);
  sorteios.forEach(s => {
    const c = contagemBlocos(s);
    const ativos = [0, 1, 2, 3, 4].filter(i => c[i] >= 4);
    ativos.forEach(i => {
      matriz[i][i]++;
      ativos.forEach(j => { if (i !== j) matriz[i][j]++; });
    });
  });

  return { freq_individual: freqIndividual, combinacoes, coocorrencia: matriz };
}

function calcFinanceiroJS(sorteiosMeta) {
  const registros = sorteiosMeta.filter(m => m.valor_premio != null);
  const totalPremiosPagos = registros.reduce((a, r) => a + r.valor_premio * (r.ganhadores || 0), 0);
  const mediaPremioFaixa1 = registros.length
    ? +(registros.reduce((a, r) => a + r.valor_premio, 0) / registros.length).toFixed(2) : null;
  const maior = registros.length ? registros.reduce((m, r) => (r.valor_premio > m.valor_premio ? r : m)) : null;
  const menor = registros.length ? registros.reduce((m, r) => (r.valor_premio < m.valor_premio ? r : m)) : null;
  const totalAcumulados = registros.filter(r => r.acumulado).length;
  const resumo = r => (r ? { valor: r.valor_premio, concurso: r.concurso, data: r.data } : null);
  const total = sorteiosMeta.length;
  return {
    total_premios_pagos: +totalPremiosPagos.toFixed(2),
    media_premio_faixa1: mediaPremioFaixa1,
    maior_premio: resumo(maior),
    menor_premio: resumo(menor),
    total_acumulados: totalAcumulados,
    total_sorteios: total,
    pct_acumulados: total ? +(totalAcumulados / total * 100).toFixed(1) : 0,
  };
}

function calcFaixaSurpresaJS(sorteiosMeta) {
  let maiorGap = 0, gapAtual = 0;
  const eventos = [];
  sorteiosMeta.forEach(m => {
    const g = m.ganhadores_surpresa || 0;
    if (g > 0) {
      eventos.push({ concurso: m.concurso, data: m.data, ganhadores: g, valor: m.valor_surpresa });
      maiorGap = Math.max(maiorGap, gapAtual);
      gapAtual = 0;
    } else {
      gapAtual++;
    }
  });
  maiorGap = Math.max(maiorGap, gapAtual);
  const n = sorteiosMeta.length;
  const qtd = eventos.length;
  const mediaGanhadores = qtd ? +(eventos.reduce((a, e) => a + e.ganhadores, 0) / qtd).toFixed(1) : null;
  const maisRecente = eventos.length ? eventos[eventos.length - 1] : null;
  return {
    total_sorteios: n,
    qtd_sorteios_surpresa: qtd,
    pct_sorteios_surpresa: n ? +(qtd / n * 100).toFixed(1) : 0,
    media_ganhadores_surpresa: mediaGanhadores,
    mais_recente: maisRecente,
    maior_gap_sem_surpresa: maiorGap,
  };
}

// espelha CUSTO_LOTOMANIA/FAIXA_LOTOMANIA/PRIORIDADE_FAIXA_LOTOMANIA/
// calc_jogos_lotomania do Python — usado por calcJogosLotomaniaJS (mesclagem
// client-side de múltiplos anos) e pelo simulador de apostas (ambos precisam
// recalcular sobre um conjunto de sorteios que não existe pré-computado).
const CUSTO_LOTOMANIA_JS = 3.00;
const CAMPO_VALOR_FAIXA_LOTOMANIA_JS = {
  20: 'valor_premio', 19: 'valor_dezenove', 18: 'valor_dezoito', 17: 'valor_dezessete',
  16: 'valor_dezesseis', 15: 'valor_quinze', 0: 'valor_surpresa',
};
const NOME_FAIXA_LOTOMANIA_JS = {
  20: 'vinte', 19: 'dezenove', 18: 'dezoito', 17: 'dezessete', 16: 'dezesseis', 15: 'quinze', 0: 'surpresa',
};
const PRIORIDADE_FAIXA_LOTOMANIA_JS = { 20: 7, 19: 6, 18: 5, 17: 4, 16: 3, 15: 2, 0: 1 };

function calcJogosLotomaniaJS(jogosDict, sorteiosRaw, sorteiosMeta) {
  const n = sorteiosRaw.length;
  const resultado = [];
  Object.entries(jogosDict).forEach(([nome, numeros]) => {
    const conjunto = new Set(numeros);
    const contagem = { 20: 0, 19: 0, 18: 0, 17: 0, 16: 0, 15: 0, 0: 0 };
    const historico = [];
    let ganho = 0;
    const saldoEvolucao = [];
    let acumulado = 0;
    let melhor = { acertos: null, concurso: null, data: null };
    let melhorPrioridade = 0;
    sorteiosRaw.forEach((s, i) => {
      const acertos = s.filter(d => conjunto.has(d)).length;
      let premio = 0;
      const meta = sorteiosMeta[i] || {};
      // faixa "quinze" só existe a partir do concurso 1653 (29/04/2016) —
      // valor_quinze null/undefined em sorteios antigos significa que a
      // faixa não existia, não que ninguém ganhou (ver montar_linha no Python)
      const existeFaixa = acertos !== 15 || (meta.valor_quinze !== undefined && meta.valor_quinze !== null);
      if (acertos in contagem && existeFaixa) {
        contagem[acertos]++;
        const campo = CAMPO_VALOR_FAIXA_LOTOMANIA_JS[acertos];
        premio = meta[campo] || 0;
        historico.push({ concurso: meta.concurso, data: meta.data, acertos, faixa: NOME_FAIXA_LOTOMANIA_JS[acertos], premio: +premio.toFixed(2) });
        ganho += premio;
        const prioridade = PRIORIDADE_FAIXA_LOTOMANIA_JS[acertos];
        if (prioridade > melhorPrioridade) {
          melhorPrioridade = prioridade;
          melhor = { acertos, concurso: meta.concurso, data: meta.data };
        }
      }
      acumulado += premio - CUSTO_LOTOMANIA_JS;
      saldoEvolucao.push(+acumulado.toFixed(2));
    });
    const gasto = +(CUSTO_LOTOMANIA_JS * n).toFixed(2);
    ganho = +ganho.toFixed(2);
    const saldo = +(ganho - gasto).toFixed(2);
    const roi = gasto ? +(saldo / gasto * 100).toFixed(1) : 0;
    const totalPremiado = Object.values(contagem).reduce((a, b) => a + b, 0);
    resultado.push({
      nome, numeros: [...numeros].sort((a, b) => a - b), contagem,
      total_premiado: totalPremiado,
      pct_premiado: n ? +(totalPremiado / n * 100).toFixed(1) : 0,
      gasto, ganho, saldo, roi, melhor, historico, saldo_evolucao: saldoEvolucao,
    });
  });
  resultado.sort((a, b) => b.saldo - a.saldo);
  return resultado;
}

function montarBundleFiltradoPorNumeros(sorteios, bundleBase) {
  const { distTamanho, topPorTamanho } = calcSequenciasJS(sorteios);
  return {
    // totalPeriodoBase: total do período (não do subconjunto filtrado por
    // número) — blocos/financeiro/faixa_surpresa não são recalculados pra
    // essa combinação de dezenas (mesma exceção documentada nas outras duas
    // loterias), então continuam refletindo o total do período original.
    meta: { total: sorteios.length, totalPeriodoBase: bundleBase.meta.total },
    frequencia: calcFrequenciaJS(sorteios),
    atraso: calcAtrasoJS(sorteios),
    pares_impares: calcParesImparesJS(sorteios),
    faixas: calcFaixasJS(sorteios),
    somas: calcSomaJS(sorteios),
    seq_dist_tamanho: distTamanho,
    seq_top_por_tamanho: topPorTamanho,
    coocorrencia: calcCoocorrenciaJS(sorteios, 20),
    anticorrelacao: calcAntiCorrelacaoJS(sorteios, 15),
    tendencia: calcTendenciaJS(sorteios),
    ciclo_medio: calcCicloMedioJS(sorteios),
    repeticao_anterior: calcRepeticaoAnteriorJS(sorteios),
    blocos: bundleBase.blocos,
    faixa_surpresa: bundleBase.faixa_surpresa,
    sorteios_raw: sorteios,
  };
}

function aplicarFiltroNumeros() {
  const hint = document.getElementById('numgrid-hint');
  const btnLimpar = document.getElementById('numgrid-limpar');
  const bundleBase = bundleAtivo();
  if (!bundleBase) return;
  if (numerosSelecionados.size === 0) {
    btnLimpar.style.display = 'none';
    hint.textContent = 'Clique em uma ou mais dezenas para filtrar todos os gráficos abaixo pela combinação escolhida.';
    renderPeriodoCompleto(bundleBase);
    return;
  }
  btnLimpar.style.display = '';
  const selecionadas = [...numerosSelecionados].sort((a, b) => a - b);
  const rotulo = selecionadas.map(n => String(n).padStart(2, '0')).join(', ');
  const sorteiosFiltrados = (bundleBase.sorteios_raw || DATA.sorteios_raw)
    .filter(s => selecionadas.every(n => s.includes(n)));
  if (!sorteiosFiltrados.length) {
    hint.textContent = `Dezenas ${rotulo}: nenhum sorteio encontrado com essa combinação no período atual.`;
    return;
  }
  hint.textContent = `Dezenas ${rotulo}: ${sorteiosFiltrados.length} sorteio(s) encontrado(s) — gráficos abaixo filtrados por essa combinação.`;
  renderPeriodoCompleto(montarBundleFiltradoPorNumeros(sorteiosFiltrados, bundleBase));
}

{
  const grid = document.getElementById('numgrid');
  grid.addEventListener('click', (ev) => {
    const cell = ev.target.closest('.numgrid-cell');
    if (!cell) return;
    const n = +cell.dataset.num;
    if (numerosSelecionados.has(n)) numerosSelecionados.delete(n); else numerosSelecionados.add(n);
    cell.classList.toggle('selecionada', numerosSelecionados.has(n));
    aplicarFiltroNumeros();
  });
  document.getElementById('numgrid-limpar').addEventListener('click', () => {
    numerosSelecionados.clear();
    grid.querySelectorAll('.selecionada').forEach(c => c.classList.remove('selecionada'));
    aplicarFiltroNumeros();
  });
}

// ── função central: aplica um período a TODOS os elementos do dashboard ─────
let periodoAtualId = '__todos__';
let historicoInicializado = false;
// quando 2+ anos são selecionados no filtro multi-ano, não existe um
// DATA.periodos[id] pré-computado pra essa combinação (2^N seria inviável no
// Python); em vez disso o bundle é montado em JS (calcularBundleCompletoJS) e
// guardado aqui — bundleAtivo() abstrai de onde vem o bundle corrente pros
// consumidores (numgrid, hot/cold etc.) não precisarem saber a diferença.
let modoMultiAno = false;
let bundleAtualMerged = null;

function resolverBundlePeriodo(periodoId) {
  return periodoId === '__todos__' ? DATA : DATA.periodos[periodoId];
}

function bundleAtivo() {
  return modoMultiAno ? bundleAtualMerged : resolverBundlePeriodo(periodoAtualId);
}

function aplicarPeriodo(periodoId) {
  const bundle = resolverBundlePeriodo(periodoId);
  if (!bundle) return;
  modoMultiAno = false;
  bundleAtualMerged = null;
  periodoAtualId = periodoId;
  numerosSelecionados.clear();
  const btnLimpar = document.getElementById('numgrid-limpar');
  const hint = document.getElementById('numgrid-hint');
  if (btnLimpar) btnLimpar.style.display = 'none';
  if (hint) hint.textContent = 'Clique em uma ou mais dezenas para filtrar todos os gráficos abaixo pela combinação escolhida.';
  renderNumGrid(bundle);
  renderPeriodoCompleto(bundle);
  renderFinanceiro(bundle);
  renderSurpresa(bundle);
  renderJogos(bundle, periodoId);
  renderBannerPeriodo(periodoId, bundle);
  renderBlocosMensal(periodoId);
  aplicarFiltroPeriodoHistorico(periodoId);
}

// ── mesclagem client-side de múltiplos anos selecionados ────────────────────
function calcularBundleCompletoJS(sorteiosRaw, sorteiosMeta) {
  const n = sorteiosRaw.length;
  const { distTamanho, topPorTamanho } = calcSequenciasJS(sorteiosRaw);
  return {
    meta: {
      total: n,
      inicio: n ? sorteiosMeta[0].data : null,
      fim: n ? sorteiosMeta[n - 1].data : null,
    },
    frequencia: calcFrequenciaJS(sorteiosRaw),
    atraso: calcAtrasoJS(sorteiosRaw),
    pares_impares: calcParesImparesJS(sorteiosRaw),
    faixas: calcFaixasJS(sorteiosRaw),
    somas: calcSomaJS(sorteiosRaw),
    seq_dist_tamanho: distTamanho,
    seq_top_por_tamanho: topPorTamanho,
    coocorrencia: calcCoocorrenciaJS(sorteiosRaw, 20),
    anticorrelacao: calcAntiCorrelacaoJS(sorteiosRaw, 15),
    tendencia: calcTendenciaJS(sorteiosRaw),
    ciclo_medio: calcCicloMedioJS(sorteiosRaw),
    repeticao_anterior: calcRepeticaoAnteriorJS(sorteiosRaw),
    blocos: calcBlocosJS(sorteiosRaw),
    financeiro: calcFinanceiroJS(sorteiosMeta),
    faixa_surpresa: calcFaixaSurpresaJS(sorteiosMeta),
    jogos: Object.keys(DATA.jogos_config || {}).length
      ? calcJogosLotomaniaJS(DATA.jogos_config, sorteiosRaw, sorteiosMeta) : null,
    sorteios_raw: sorteiosRaw,
    sorteios_meta: sorteiosMeta,
  };
}

function renderBannerPeriodoMulti(anos, bundle) {
  const banner = document.getElementById('periodo-banner');
  const label = [...anos].sort().join(' + ');
  banner.textContent = `⚡ Exibindo: ${label} · ${bundle.meta.total} sorteios`;
  banner.style.display = 'block';
}

function aplicarFiltroPeriodoHistoricoMulti(anos) {
  if (!historicoInicializado) return;
  const anosSet = new Set(anos);
  const raiz = document.getElementById('historico-arvore');
  raiz.querySelectorAll('.hist-mes').forEach(mesDiv => {
    mesDiv.classList.toggle('hist-fora-periodo', !anosSet.has(mesDiv.dataset.ano));
  });
  raiz.querySelectorAll('.hist-ano').forEach(anoDiv => {
    const algumMesVisivel = [...anoDiv.querySelectorAll('.hist-mes')]
      .some(mesDiv => !mesDiv.classList.contains('hist-fora-periodo'));
    anoDiv.classList.toggle('hist-fora-periodo', !algumMesVisivel);
  });
}

function aplicarPeriodoMultiAno(anos) {
  // funde sorteios_raw + sorteios_meta dos anos selecionados (cada
  // DATA.periodos[ano] já vem com os dois, precomputados no Python), ordena
  // por concurso pra manter a ordem cronológica (tendência depende disso) e
  // recalcula tudo no cliente com as mesmas funções calc*JS da grade interativa.
  const pares = [];
  anos.forEach(ano => {
    const p = DATA.periodos[ano];
    if (!p) return;
    p.sorteios_raw.forEach((s, i) => pares.push([s, p.sorteios_meta[i]]));
  });
  pares.sort((a, b) => a[1].concurso - b[1].concurso);
  const sorteiosRaw = pares.map(x => x[0]);
  const sorteiosMeta = pares.map(x => x[1]);

  modoMultiAno = true;
  periodoAtualId = 'MULTI:' + [...anos].sort().join(',');
  bundleAtualMerged = calcularBundleCompletoJS(sorteiosRaw, sorteiosMeta);

  numerosSelecionados.clear();
  const btnLimpar = document.getElementById('numgrid-limpar');
  const hint = document.getElementById('numgrid-hint');
  if (btnLimpar) btnLimpar.style.display = 'none';
  if (hint) hint.textContent = 'Clique em uma ou mais dezenas para filtrar todos os gráficos abaixo pela combinação escolhida.';
  renderNumGrid(bundleAtualMerged);
  renderPeriodoCompleto(bundleAtualMerged);
  renderFinanceiro(bundleAtualMerged);
  renderSurpresa(bundleAtualMerged);
  renderJogos(bundleAtualMerged, periodoAtualId);
  renderBannerPeriodoMulti(anos, bundleAtualMerged);
  renderBlocosMensalMulti(anos);
  aplicarFiltroPeriodoHistoricoMulti(anos);
}

// ── seletor de período cascateado: Ano(s) — multi-select (sempre visível) →
// tipo de período (Ano completo/Semestre/Trimestre/Bimestre/Mês, só aparece
// com EXATAMENTE 1 ano selecionado) → intervalo específico. 0 anos = "Todos
// os dados"; 2+ anos = mescla os anos selecionados (sem subdivisão). Trocar a
// seleção de anos sempre reseta os níveis seguintes pro padrão. ─────────────
let periodoCascata = { anos: new Set(), tipo: 'ano', subId: null };
let mostrarTodosAnos = false;
const MESES_LABEL_CASCATA = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
const TIPOS_NIVEL2 = [
  ['ano', 'Ano completo'], ['semestre', 'Semestre'], ['trimestre', 'Trimestre'],
  ['bimestre', 'Bimestre'], ['mes', 'Mês'],
];
const PREFIXO_TIPO = { semestre: 'S', trimestre: 'T', bimestre: 'B', mes: 'M' };
const LIMITE_ANOS_SEM_EXPANDIR = 10;
const QTD_ANOS_RECENTES_PADRAO = 5;

function periodoFinalDaCascata() {
  if (periodoCascata.anos.size === 0) return '__todos__';
  const ano = [...periodoCascata.anos][0];
  if (periodoCascata.tipo === 'ano') return ano;
  return periodoCascata.subId || ano;
}

function toggleAnoSelecionado(ano) {
  if (periodoCascata.anos.has(ano)) periodoCascata.anos.delete(ano);
  else periodoCascata.anos.add(ano);
  periodoCascata.tipo = 'ano';
  periodoCascata.subId = null;
  renderSeletorCascata();
}

function renderSeletorCascata() {
  const container = document.getElementById('period-selector');
  const todosBtn = document.getElementById('period-todos-btn');
  container.innerHTML = '';
  todosBtn.classList.toggle('active', periodoCascata.anos.size === 0);

  const disponiveis = DATA.periodos_disponiveis || [];
  const anos = [...new Set(disponiveis.filter(p => p.tipo === 'ano').map(p => p.id))].sort().reverse();

  const linhaAno = document.createElement('div');
  linhaAno.className = 'period-row';
  const labelAno = document.createElement('span');
  labelAno.className = 'tipo-label';
  labelAno.textContent = 'Ano(s):';
  linhaAno.appendChild(labelAno);

  const wrapAnos = document.createElement('div');
  wrapAnos.className = 'period-anos-wrap';
  const precisaExpandir = anos.length > LIMITE_ANOS_SEM_EXPANDIR;
  const anosVisiveis = (precisaExpandir && !mostrarTodosAnos) ? anos.slice(0, QTD_ANOS_RECENTES_PADRAO) : anos;
  anosVisiveis.forEach(ano => {
    const lbl = document.createElement('label');
    lbl.className = 'period-ano-check' + (periodoCascata.anos.has(ano) ? ' selecionado' : '');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = periodoCascata.anos.has(ano);
    input.addEventListener('change', () => toggleAnoSelecionado(ano));
    lbl.appendChild(input);
    lbl.appendChild(document.createTextNode(ano));
    wrapAnos.appendChild(lbl);
  });
  if (precisaExpandir) {
    const btnVerTodos = document.createElement('button');
    btnVerTodos.type = 'button';
    btnVerTodos.className = 'period-ver-todos-btn';
    btnVerTodos.textContent = mostrarTodosAnos ? '▲ Ver menos anos' : `▼ Ver todos os anos (${anos.length})`;
    btnVerTodos.addEventListener('click', () => { mostrarTodosAnos = !mostrarTodosAnos; renderSeletorCascata(); });
    wrapAnos.appendChild(btnVerTodos);
  }
  linhaAno.appendChild(wrapAnos);
  container.appendChild(linhaAno);

  if (periodoCascata.anos.size === 0) {
    aplicarPeriodo('__todos__');
    return;
  }

  if (periodoCascata.anos.size > 1) {
    aplicarPeriodoMultiAno([...periodoCascata.anos]);
    return;
  }

  const anoUnico = [...periodoCascata.anos][0];

  const linhaTipo = document.createElement('div');
  linhaTipo.className = 'period-row period-nivel';
  const labelTipo = document.createElement('span');
  labelTipo.className = 'tipo-label';
  labelTipo.textContent = 'Período:';
  linhaTipo.appendChild(labelTipo);
  const opcoesTipo = document.createElement('div');
  opcoesTipo.className = 'period-nivel2-opcoes';
  TIPOS_NIVEL2.forEach(([valor, texto]) => {
    const lbl = document.createElement('label');
    lbl.className = 'period-radio';
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'period-nivel2';
    input.checked = periodoCascata.tipo === valor;
    input.addEventListener('change', () => {
      periodoCascata.tipo = valor;
      periodoCascata.subId = null;
      renderSeletorCascata();
    });
    lbl.appendChild(input);
    lbl.appendChild(document.createTextNode(texto));
    opcoesTipo.appendChild(lbl);
  });
  linhaTipo.appendChild(opcoesTipo);
  container.appendChild(linhaTipo);

  if (periodoCascata.tipo === 'ano') {
    aplicarPeriodo(anoUnico);
    return;
  }

  const linhaSub = document.createElement('div');
  linhaSub.className = 'period-row period-nivel';
  const labelSub = document.createElement('span');
  labelSub.className = 'tipo-label';
  labelSub.innerHTML = '&nbsp;';
  linhaSub.appendChild(labelSub);
  const scrollSub = document.createElement('div');
  scrollSub.className = 'period-row-scroll';

  if (periodoCascata.tipo === 'mes') {
    const idsExistentes = new Set(
      disponiveis.filter(p => p.tipo === 'mes' && p.id.startsWith(anoUnico + '-M')).map(p => p.id)
    );
    for (let m = 1; m <= 12; m++) {
      const id = `${anoUnico}-M${String(m).padStart(2, '0')}`;
      const existe = idsExistentes.has(id);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'period-btn' + (periodoCascata.subId === id ? ' active' : '');
      btn.textContent = MESES_LABEL_CASCATA[m - 1];
      btn.dataset.periodo = id;
      btn.disabled = !existe;
      if (existe) {
        btn.addEventListener('click', () => { periodoCascata.subId = id; renderSeletorCascata(); });
      }
      scrollSub.appendChild(btn);
    }
    if (!periodoCascata.subId || !idsExistentes.has(periodoCascata.subId)) {
      periodoCascata.subId = idsExistentes.size ? [...idsExistentes].sort()[0] : null;
    }
  } else {
    const prefixo = PREFIXO_TIPO[periodoCascata.tipo];
    const opcoes = disponiveis
      .filter(p => p.tipo === periodoCascata.tipo && p.id.startsWith(anoUnico + '-' + prefixo))
      .sort((a, b) => a.id.localeCompare(b.id));
    if (!periodoCascata.subId || !opcoes.some(p => p.id === periodoCascata.subId)) {
      periodoCascata.subId = opcoes.length ? opcoes[0].id : null;
    }
    opcoes.forEach(p => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'period-btn' + (periodoCascata.subId === p.id ? ' active' : '');
      btn.textContent = p.label;
      btn.dataset.periodo = p.id;
      btn.addEventListener('click', () => { periodoCascata.subId = p.id; renderSeletorCascata(); });
      scrollSub.appendChild(btn);
    });
  }

  linhaSub.appendChild(scrollSub);
  container.appendChild(linhaSub);

  aplicarPeriodo(periodoFinalDaCascata());
}

document.getElementById('period-todos-btn').addEventListener('click', () => {
  periodoCascata = { anos: new Set(), tipo: 'ano', subId: null };
  renderSeletorCascata();
});

renderSeletorCascata();

// ── abas de página: Análise Geral / Blocos / Histórico / Meus Jogos ──────────
{
  const paginas = [
    { tab: 'page-tab-geral', pagina: 'page-geral' },
    { tab: 'page-tab-blocos', pagina: 'page-blocos' },
    { tab: 'page-tab-historico', pagina: 'page-historico' },
    { tab: 'page-tab-jogos', pagina: 'page-jogos' },
  ];
  paginas.forEach(({ tab, pagina }) => {
    document.getElementById(tab).addEventListener('click', () => {
      paginas.forEach(({ tab: t2, pagina: p2 }) => {
        const ativa = t2 === tab;
        document.getElementById(t2).classList.toggle('active', ativa);
        document.getElementById(p2).style.display = ativa ? '' : 'none';
      });
      if (tab === 'page-tab-historico') inicializarHistorico();
    });
  });
}

// ── Blocos por período (heatmap mensal — histórico completo só em "Todos";
// condensado aos meses do período ativo quando há filtro de ano/semestre/
// trimestre/bimestre; oculto quando o filtro já é 1 mês só) ─────────────────
function anoDoPeriodo(periodoId) {
  if (periodoId === '__todos__') return null;
  return periodoId.split('-')[0];
}
function ehPeriodoMesUnico(periodoId) {
  return /-M\d{2}$/.test(periodoId);
}
function desenharTabelaBlocosMensal(dados) {
  const el = document.getElementById('blocos-heatmap-periodo');
  const nomes = ['A','B','C','D','E'];
  el.innerHTML = '';
  if (!dados.length) {
    el.innerHTML = '<p style="color:var(--muted)">Sem dados.</p>';
    return;
  }
  const todasMedias = dados.flatMap(d => d.medias);
  const minV = Math.min(...todasMedias), maxV = Math.max(...todasMedias);
  const table = document.createElement('table');
  table.innerHTML = `<thead><tr><th>Mês</th>${nomes.map(n => `<th>${n}</th>`).join('')}</tr></thead>`;
  const tbody = document.createElement('tbody');
  dados.forEach(d => {
    const tr = document.createElement('tr');
    let celulas = `<td style="color:var(--muted)">${d.periodo}</td>`;
    d.medias.forEach(v => {
      const t = maxV > minV ? (v - minV) / (maxV - minV) : 0;
      celulas += `<td style="background:${hexToRgba(corGradienteAmbar(t), .35)}; text-align:center;">${v}</td>`;
    });
    tr.innerHTML = celulas;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  el.appendChild(table);
}

function renderBlocosMensal(periodoId) {
  const wrap = document.getElementById('blocos-mensal-wrap');
  const tituloEl = document.getElementById('blocos-heatmap-titulo');

  if (ehPeriodoMesUnico(periodoId)) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = '';

  let dados = DATA.blocos_periodo || [];
  let titulo = '🗓️ Média por mês — histórico completo';
  if (periodoId !== '__todos__') {
    const ano = anoDoPeriodo(periodoId);
    dados = dados.filter(d => d.periodo.startsWith(ano + '-'));
    titulo = '🗓️ Blocos no período selecionado';
  }
  tituloEl.textContent = titulo;
  desenharTabelaBlocosMensal(dados);
}

function renderBlocosMensalMulti(anos) {
  const wrap = document.getElementById('blocos-mensal-wrap');
  const tituloEl = document.getElementById('blocos-heatmap-titulo');
  wrap.style.display = '';
  const anosSet = new Set(anos);
  const dados = (DATA.blocos_periodo || []).filter(d => anosSet.has(d.periodo.split('-')[0]));
  tituloEl.textContent = '🗓️ Blocos no período selecionado';
  desenharTabelaBlocosMensal(dados);
}

// ── Sorteios repetidos (curiosidade — sempre histórico completo) ─────────────
{
  const el = document.getElementById('repeticoes-conteudo');
  const reps = DATA.repeticoes || [];
  const prob = DATA.prob_repeticao_pct;
  const totalComb = DATA.total_combinacoes;
  const probTxt = totalComb
    ? `A probabilidade de pelo menos 1 repetição depois de ${DATA.meta.total} sorteios é de aproximadamente ${prob}% (1 em ${totalComb.toLocaleString('pt-BR')} combinações possíveis).`
    : '';
  if (!reps.length) {
    el.innerHTML = `
      <div class="repet-ok">
        <span class="icone">✅</span>
        <div>
          <p>Nenhum sorteio idêntico encontrado em ${DATA.meta.total} concursos analisados.</p>
          <p class="prob">${probTxt}</p>
        </div>
      </div>`;
  } else {
    let html = `<p style="color:var(--muted); font-size:12px; margin-bottom:14px;">${reps.length} ocorrência${reps.length === 1 ? '' : 's'} encontrada${reps.length === 1 ? '' : 's'}. ${probTxt}</p>`;
    reps.forEach((rep, i) => {
      const dezenasTxt = rep.numeros.map(n => String(n).padStart(2, '0')).join(' · ');
      const sorteiosHtml = rep.sorteios.map(s => `<div class="repet-sorteio">→ Concurso <b>${s.concurso}</b> (${s.data})</div>`).join('');
      html += `
        <div class="repet-item">
          <div class="repet-item-titulo">Ocorrência ${i + 1} — sorteado ${rep.vezes}x</div>
          <div class="repet-numeros">${dezenasTxt}</div>
          ${sorteiosHtml}
        </div>`;
    });
    el.innerHTML = html;
  }
}

// ── Simulador de apostas — separadores flexíveis + múltiplos jogos. Universo
// 00-99, exatamente 50 dezenas por jogo (diferente da Lotofácil/Mega-Sena) ───
function parseDezenasBrutasLotomania(texto) {
  return texto.split(/[\s,;.\-]+/).map(s => s.trim()).filter(s => s.length > 0).map(Number);
}
function validarJogoTextoLotomania(texto) {
  const brutas = parseDezenasBrutasLotomania(texto);
  if (!brutas.length) return { status: 'vazio', validos: [] };
  for (const n of brutas) {
    if (!Number.isInteger(n) || n < 0 || n > 99) {
      return { status: 'erro', motivo: 'range', numero: n, validos: [] };
    }
  }
  const vistos = new Set();
  for (const n of brutas) {
    if (vistos.has(n)) return { status: 'erro', motivo: 'repetido', numero: n, validos: [] };
    vistos.add(n);
  }
  if (brutas.length === 50) return { status: 'ok', validos: brutas };
  return { status: 'parcial', validos: brutas, count: brutas.length };
}

{
  const listaEl = document.getElementById('sim-jogos-lista');
  const btnAdd = document.getElementById('sim-btn-add');
  const btnVerificar = document.getElementById('sim-btn');
  const errEl = document.getElementById('sim-error');
  const resultEl = document.getElementById('sim-result');

  function renumerarJogos() {
    listaEl.querySelectorAll('.sim-jogo-row').forEach((row, i) => {
      row.querySelector('.sim-jogo-label').textContent = 'Jogo ' + (i + 1);
    });
    const rows = listaEl.querySelectorAll('.sim-jogo-row');
    rows.forEach(row => {
      row.querySelector('.sim-jogo-remove').style.visibility = rows.length > 1 ? 'visible' : 'hidden';
    });
  }

  function atualizarBadge(row) {
    const inputEl = row.querySelector('.sim-jogo-input');
    const badge = row.querySelector('.sim-jogo-badge');
    const v = validarJogoTextoLotomania(inputEl.value);
    badge.classList.remove('ok', 'parcial', 'erro');
    if (v.status === 'vazio') {
      badge.textContent = '';
    } else if (v.status === 'ok') {
      badge.textContent = '✓ 50/50';
      badge.classList.add('ok');
    } else if (v.status === 'erro') {
      badge.textContent = v.motivo === 'repetido'
        ? `nº ${String(v.numero).padStart(2,'0')} repetido`
        : `nº ${String(v.numero).padStart(2,'0')} inválido`;
      badge.classList.add('erro');
    } else {
      badge.textContent = `${v.validos.length}/50 números`;
      badge.classList.add('parcial');
    }
  }

  function criarLinhaJogo() {
    const row = document.createElement('div');
    row.className = 'sim-jogo-row';

    const label = document.createElement('span');
    label.className = 'sim-jogo-label';
    label.textContent = 'Jogo';
    row.appendChild(label);

    const inputEl = document.createElement('input');
    inputEl.type = 'text';
    inputEl.className = 'sim-jogo-input';
    inputEl.placeholder = 'ex: 00, 02 05-06;07 09.11 13 ... (exatamente 50 números entre 00 e 99)';
    inputEl.addEventListener('input', () => atualizarBadge(row));
    row.appendChild(inputEl);

    const badge = document.createElement('span');
    badge.className = 'sim-jogo-badge';
    row.appendChild(badge);

    const btnRemove = document.createElement('button');
    btnRemove.type = 'button';
    btnRemove.className = 'sim-jogo-remove';
    btnRemove.textContent = '🗑️';
    btnRemove.addEventListener('click', () => {
      if (listaEl.querySelectorAll('.sim-jogo-row').length <= 1) return;
      row.remove();
      renumerarJogos();
    });
    row.appendChild(btnRemove);

    return row;
  }

  function definirQuantidade(n) {
    listaEl.innerHTML = '';
    for (let i = 0; i < n; i++) listaEl.appendChild(criarLinhaJogo());
    renumerarJogos();
  }

  document.querySelectorAll('#sim-qtd-selector input[name="sim-qtd"]').forEach(radio => {
    radio.addEventListener('change', () => { if (radio.checked) definirQuantidade(+radio.value); });
  });
  btnAdd.addEventListener('click', () => {
    listaEl.appendChild(criarLinhaJogo());
    renumerarJogos();
  });

  definirQuantidade(1); // estado inicial

  function calcularResultadoLotomania(numeros, sorteiosRaw, sorteiosMeta) {
    const aposta = new Set(numeros);
    const pontos = { 0: 0, 15: 0, 16: 0, 17: 0, 18: 0, 19: 0, 20: 0 };
    const concursosPontuados = [];
    let ganho = 0;
    sorteiosRaw.forEach((s, i) => {
      const acertos = s.filter(d => aposta.has(d)).length;
      const meta = sorteiosMeta[i];
      // faixa "quinze" só existe a partir do concurso 1653 (29/04/2016)
      const existeFaixa = acertos !== 15 || (meta && meta.valor_quinze !== undefined && meta.valor_quinze !== null);
      if (acertos in pontos && existeFaixa) {
        pontos[acertos]++;
        if (meta) {
          const campo = CAMPO_VALOR_FAIXA_LOTOMANIA_JS[acertos];
          const premio = meta[campo] || 0;
          ganho += premio;
          concursosPontuados.push({ concurso: meta.concurso, data: meta.data, acertos, premio: +premio.toFixed(2) });
        }
      }
    });
    concursosPontuados.sort((a, b) => b.concurso - a.concurso);
    const totalPremios = Object.values(pontos).reduce((a, b) => a + b, 0);
    const melhorPrioridade = concursosPontuados.length
      ? Math.max(...concursosPontuados.map(c => PRIORIDADE_FAIXA_LOTOMANIA_JS[c.acertos])) : 0;
    const totalSorteios = sorteiosRaw.length;
    const custo = +(CUSTO_LOTOMANIA_JS * totalSorteios).toFixed(2);
    ganho = +ganho.toFixed(2);
    const saldo = +(ganho - custo).toFixed(2);
    const roi = custo ? +(saldo / custo * 100).toFixed(1) : 0;
    return { numeros, pontos, concursosPontuados, totalPremios, melhorPrioridade, totalSorteios, custo, ganho, saldo, roi };
  }

  function construirDetalhePainelLotomania(resultado) {
    const painel = document.createElement('div');
    painel.className = 'sim-detalhe-painel';
    if (!resultado.concursosPontuados.length) {
      painel.innerHTML = '<span style="color:var(--muted); font-size:12px;">Nenhum sorteio premiado.</span>';
      return painel;
    }
    resultado.concursosPontuados.forEach(c => {
      const item = document.createElement('span');
      item.className = 'sim-detalhe-item';
      const faixaTxt = c.acertos === 0 ? 'Surpresa' : `${c.acertos} pts`;
      item.textContent = `Concurso ${c.concurso} (${c.data}) — ${faixaTxt}`;
      painel.appendChild(item);
    });
    return painel;
  }

  function formatarNumerosLotomania(numeros) {
    return [...numeros].sort((a, b) => a - b).map(n => String(n).padStart(2, '0')).join(' ');
  }

  let ultimosResultadosLotomania = []; // guardado para o botão "copiar resultado"

  function renderizarResultadoUnicoLotomania(resultado, targetEl) {
    targetEl = targetEl || resultEl; // default preserva o comportamento original (simulador por texto)
    const total = resultado.totalSorteios;
    const table = document.createElement('table');
    table.innerHTML = `<thead><tr><th>Faixa</th><th>Vezes que ocorreu</th><th>% dos sorteios</th></tr></thead>`;
    const tbody = document.createElement('tbody');
    [20, 19, 18, 17, 16, 15, 0].forEach(p => {
      const tr = document.createElement('tr');
      const label = p === 0 ? 'Surpresa (0 pts)' : `${p} pontos`;
      tr.innerHTML = `<td class="pontos">${label}</td><td>${resultado.pontos[p]}</td><td>${(resultado.pontos[p] / total * 100).toFixed(2)}%</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    targetEl.appendChild(table);

    const finDiv = document.createElement('div');
    finDiv.className = 'mini-stats';
    finDiv.innerHTML = `
      <div>Sorteios no período<b>${total}</b></div>
      <div>Custo total<b>${formatarMoeda(resultado.custo)}</b></div>
      <div>Ganho estimado<b>${formatarMoeda(resultado.ganho)}</b></div>
      <div>Saldo<b class="${resultado.saldo >= 0 ? 'money-pos' : 'money-neg'}">${formatarMoeda(resultado.saldo)}</b></div>
      <div>ROI<b class="${resultado.roi >= 0 ? 'money-pos' : 'money-neg'}">${formatarPct(resultado.roi)}</b></div>`;
    targetEl.appendChild(finDiv);

    const btnToggle = document.createElement('button');
    btnToggle.className = 'sim-detalhe-toggle';
    btnToggle.style.marginTop = '10px';
    btnToggle.textContent = `Ver em quais concursos pontuou (${resultado.concursosPontuados.length})`;
    const painel = construirDetalhePainelLotomania(resultado);
    btnToggle.addEventListener('click', () => {
      painel.classList.toggle('aberto');
      btnToggle.textContent = painel.classList.contains('aberto')
        ? 'Esconder concursos'
        : `Ver em quais concursos pontuou (${resultado.concursosPontuados.length})`;
    });
    targetEl.appendChild(btnToggle);
    targetEl.appendChild(painel);
  }

  function renderizarResultadoComparativoLotomania(resultados) {
    const maxPremios = Math.max(...resultados.map(r => r.totalPremios));
    const maxPrioridade = Math.max(...resultados.map(r => r.melhorPrioridade));

    const table = document.createElement('table');
    table.innerHTML = `<thead><tr><th>#</th><th>20pts</th><th>19pts</th><th>18pts</th><th>17pts</th><th>16pts</th><th>15pts</th><th>Surpresa</th><th>Total prêmios</th><th>Custo</th><th>Ganho</th><th>Saldo</th><th>ROI</th></tr></thead>`;
    const tbody = document.createElement('tbody');

    resultados.forEach((r, i) => {
      const tr = document.createElement('tr');
      tr.className = 'sim-compare-row';
      if (r.totalPremios === maxPremios && maxPremios > 0) tr.classList.add('destaque-ouro');
      const trofeuIndividual = (r.melhorPrioridade === maxPrioridade && maxPrioridade > 0)
        ? `<span class="sim-trofeu" title="Melhor faixa atingida entre os jogos simulados">🏆</span>` : '';
      tr.innerHTML = `
        <td>Jogo ${i + 1}${trofeuIndividual}</td>
        <td>${r.pontos[20]}</td>
        <td>${r.pontos[19]}</td>
        <td>${r.pontos[18]}</td>
        <td>${r.pontos[17]}</td>
        <td>${r.pontos[16]}</td>
        <td>${r.pontos[15]}</td>
        <td>${r.pontos[0]}</td>
        <td>${r.totalPremios}</td>
        <td>${formatarMoeda(r.custo)}</td>
        <td>${formatarMoeda(r.ganho)}</td>
        <td class="${r.saldo >= 0 ? 'money-pos' : 'money-neg'}">${formatarMoeda(r.saldo)}</td>
        <td class="${r.roi >= 0 ? 'money-pos' : 'money-neg'}">${formatarPct(r.roi)}</td>`;
      tbody.appendChild(tr);

      const trDetalhe = document.createElement('tr');
      const tdDetalhe = document.createElement('td');
      tdDetalhe.colSpan = 13;
      const btnToggle = document.createElement('button');
      btnToggle.className = 'sim-detalhe-toggle';
      btnToggle.textContent = `Ver em quais concursos pontuou (${r.concursosPontuados.length})`;
      const painel = construirDetalhePainelLotomania(r);
      btnToggle.addEventListener('click', () => {
        painel.classList.toggle('aberto');
        btnToggle.textContent = painel.classList.contains('aberto')
          ? 'Esconder concursos'
          : `Ver em quais concursos pontuou (${r.concursosPontuados.length})`;
      });
      tdDetalhe.appendChild(btnToggle);
      tdDetalhe.appendChild(painel);
      trDetalhe.appendChild(tdDetalhe);
      tbody.appendChild(trDetalhe);
    });

    table.appendChild(tbody);
    resultEl.appendChild(table);
  }

  function copiarResultadoLotomania() {
    if (!ultimosResultadosLotomania.length) return;
    let texto;
    if (ultimosResultadosLotomania.length === 1) {
      const r = ultimosResultadosLotomania[0];
      texto = `Simulação Lotomania — ${formatarNumerosLotomania(r.numeros)}\n`
        + `20 pts: ${r.pontos[20]} | 19 pts: ${r.pontos[19]} | 18 pts: ${r.pontos[18]} | 17 pts: ${r.pontos[17]} | `
        + `16 pts: ${r.pontos[16]} | 15 pts: ${r.pontos[15]} | Surpresa: ${r.pontos[0]} | Total prêmios: ${r.totalPremios}\n`
        + `Sorteios: ${r.totalSorteios} | Custo: ${formatarMoeda(r.custo)} | Ganho: ${formatarMoeda(r.ganho)} | `
        + `Saldo: ${formatarMoeda(r.saldo)} | ROI: ${formatarPct(r.roi)}`;
    } else {
      const linhas = ['#\t20pts\t19pts\t18pts\t17pts\t16pts\t15pts\tSurpresa\tTotal prêmios\tCusto\tGanho\tSaldo\tROI'];
      ultimosResultadosLotomania.forEach((r, i) => {
        linhas.push(`Jogo ${i + 1}\t${r.pontos[20]}\t${r.pontos[19]}\t${r.pontos[18]}\t${r.pontos[17]}\t${r.pontos[16]}\t${r.pontos[15]}\t${r.pontos[0]}\t${r.totalPremios}\t${formatarMoeda(r.custo)}\t${formatarMoeda(r.ganho)}\t${formatarMoeda(r.saldo)}\t${formatarPct(r.roi)}`);
      });
      texto = linhas.join('\n');
    }
    navigator.clipboard.writeText(texto).then(() => {
      btnCopiar.textContent = '✓ Copiado!';
      setTimeout(() => { btnCopiar.textContent = '📋 Copiar resultado'; }, 2000);
    }).catch(() => {
      btnCopiar.textContent = 'Não foi possível copiar';
      setTimeout(() => { btnCopiar.textContent = '📋 Copiar resultado'; }, 2000);
    });
  }

  const btnCopiar = document.createElement('button');
  btnCopiar.type = 'button';
  btnCopiar.className = 'sim-copiar-btn';
  btnCopiar.textContent = '📋 Copiar resultado';
  btnCopiar.style.display = 'none';
  btnCopiar.addEventListener('click', copiarResultadoLotomania);

  btnVerificar.addEventListener('click', () => {
    errEl.style.display = 'none';
    resultEl.innerHTML = '';
    btnCopiar.style.display = 'none';
    document.getElementById('sim-periodo-aviso').style.display = 'none';

    const linhas = [...listaEl.querySelectorAll('.sim-jogo-row')];
    const validacoes = linhas.map(row => validarJogoTextoLotomania(row.querySelector('.sim-jogo-input').value));
    const validos = [];
    let ignorados = 0;
    validacoes.forEach(v => {
      if (v.status === 'ok') validos.push(v.validos);
      else if (v.status !== 'vazio') ignorados++;
    });

    if (!validos.length) {
      if (linhas.length === 1) {
        const v = validacoes[0];
        if (v.status === 'erro' && v.motivo === 'range') {
          errEl.textContent = `Número ${String(v.numero).padStart(2,'0')} inválido — use valores entre 00 e 99.`;
        } else if (v.status === 'erro' && v.motivo === 'repetido') {
          errEl.textContent = `Número ${String(v.numero).padStart(2,'0')} repetido.`;
        } else if (v.status === 'parcial') {
          errEl.textContent = `Informe exatamente 50 números (você informou ${v.count}).`;
        } else {
          errEl.textContent = 'Informe exatamente 50 números entre 00 e 99, sem repetição.';
        }
      } else {
        errEl.textContent = 'Nenhum jogo válido — cada um precisa de exatamente 50 números entre 00 e 99, sem repetição.';
      }
      errEl.style.display = 'block';
      return;
    }

    // simula contra o período de análise ativo no momento (bundleAtivo() já
    // resolve "todos"/1 ano/N anos mesclados)
    const bundleSim = bundleAtivo() || DATA;
    const sorteiosRawSim = bundleSim.sorteios_raw || DATA.sorteios_raw;
    const sorteiosMetaSim = bundleSim.sorteios_meta || DATA.sorteios_meta || [];
    ultimosResultadosLotomania = validos.map(v => calcularResultadoLotomania(v, sorteiosRawSim, sorteiosMetaSim));

    if (ignorados > 0) {
      const aviso = document.createElement('div');
      aviso.className = 'sim-aviso';
      aviso.textContent = `${ignorados} jogo(s) ignorado(s) por estarem incompletos ou inválidos.`;
      resultEl.appendChild(aviso);
    }

    if (ultimosResultadosLotomania.length === 1) {
      renderizarResultadoUnicoLotomania(ultimosResultadosLotomania[0]);
    } else {
      renderizarResultadoComparativoLotomania(ultimosResultadosLotomania);
    }
    resultEl.appendChild(btnCopiar);
    btnCopiar.style.display = 'inline-block';
  });

  // ── Simulador de Jogo (bolinhas) — mesma entrada de dados (universo 00-99,
  // exatamente 50 números), reaproveita calcularResultadoLotomania/
  // renderizarResultadoUnicoLotomania acima em vez de duplicar o cálculo. ──
  {
    const SIMBALL_MIN = 50;
    const grid = document.getElementById('simball-grid');
    const contadorEl = document.getElementById('simball-contador');
    const btnSimular = document.getElementById('simball-btn');
    const btnLimpar = document.getElementById('simball-limpar');
    const errBallEl = document.getElementById('simball-error');
    const resultBallEl = document.getElementById('simball-result');
    const selecionadas = new Set();

    function atualizarContador() {
      contadorEl.textContent = `${selecionadas.size}/${SIMBALL_MIN} selecionados`;
      contadorEl.classList.toggle('completo', selecionadas.size === SIMBALL_MIN);
      btnSimular.disabled = selecionadas.size !== SIMBALL_MIN;
      grid.querySelectorAll('.simball-cell').forEach(cell => {
        const n = +cell.dataset.num;
        cell.classList.toggle('desabilitada', selecionadas.size >= SIMBALL_MIN && !selecionadas.has(n));
      });
    }

    for (let n = 0; n <= 99; n++) {
      const cell = document.createElement('div');
      cell.className = 'simball-cell';
      cell.dataset.num = String(n);
      cell.textContent = String(n).padStart(2, '0');
      cell.addEventListener('click', () => {
        if (selecionadas.has(n)) {
          selecionadas.delete(n);
          cell.classList.remove('selecionada');
        } else {
          if (selecionadas.size >= SIMBALL_MIN) return;
          selecionadas.add(n);
          cell.classList.add('selecionada');
        }
        atualizarContador();
      });
      grid.appendChild(cell);
    }
    atualizarContador();

    btnLimpar.addEventListener('click', () => {
      selecionadas.clear();
      grid.querySelectorAll('.simball-cell').forEach(cell => cell.classList.remove('selecionada'));
      atualizarContador();
      errBallEl.style.display = 'none';
      resultBallEl.innerHTML = '';
    });

    btnSimular.addEventListener('click', () => {
      errBallEl.style.display = 'none';
      resultBallEl.innerHTML = '';
      if (selecionadas.size !== SIMBALL_MIN) {
        errBallEl.textContent = `Selecione exatamente ${SIMBALL_MIN} dezenas.`;
        errBallEl.style.display = 'block';
        return;
      }
      const numeros = [...selecionadas].sort((a, b) => a - b);
      const bundleSim = bundleAtivo() || DATA;
      const sorteiosRawSim = bundleSim.sorteios_raw || DATA.sorteios_raw;
      const sorteiosMetaSim = bundleSim.sorteios_meta || DATA.sorteios_meta || [];
      const resultado = calcularResultadoLotomania(numeros, sorteiosRawSim, sorteiosMetaSim);
      renderizarResultadoUnicoLotomania(resultado, resultBallEl);
    });
  }
}

// ── Histórico — árvore Ano → Mês → Sorteio ───────────────────────────────────

function expandirEl(el) {
  el.style.maxHeight = el.scrollHeight + 'px';
  const onEnd = (ev) => {
    if (ev.propertyName && ev.propertyName !== 'max-height') return;
    el.style.maxHeight = 'none';
    el.removeEventListener('transitionend', onEnd);
  };
  el.addEventListener('transitionend', onEnd);
}

function colapsarEl(el) {
  if (el.style.maxHeight === 'none' || el.style.maxHeight === '') {
    el.style.maxHeight = el.scrollHeight + 'px';
  }
  requestAnimationFrame(() => {
    requestAnimationFrame(() => { el.style.maxHeight = '0px'; });
  });
}

function abrirContainer(container) {
  if (container.style.maxHeight === 'none') return;
  const header = container.previousElementSibling;
  header.querySelector('.hist-arrow').classList.add('open');
  expandirEl(container);
}

function formatarMoedaHist(v) {
  return v == null ? '—' : v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatarDataExtenso(iso) {
  const [ano, mes, dia] = iso.split('-');
  const MESES = ['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'];
  return `${dia} de ${MESES[+mes - 1]} de ${ano}`;
}

function construirDetalheSorteio(s) {
  const div = document.createElement('div');
  div.className = 'hist-detail-inner';
  // 20 dezenas em duas linhas de 10 (ver mockup do briefing)
  const linha1 = s.dezenas.slice(0, 10).map(n => `<span class="hist-badge">${String(n).padStart(2,'0')}</span>`).join('');
  const linha2 = s.dezenas.slice(10, 20).map(n => `<span class="hist-badge">${String(n).padStart(2,'0')}</span>`).join('');
  const surpresas = s.ganhadores_surpresa != null ? s.ganhadores_surpresa : 0;
  div.innerHTML = `
    <div class="hist-detail-titulo">Concurso ${s.concurso} — ${s.dia_semana}, ${formatarDataExtenso(s.data_iso)}${s.acumulado ? ' ⭐' : ''}</div>
    <div class="hist-badges">${linha1}</div>
    <div class="hist-badges">${linha2}</div>
    <div class="hist-detail-meta">
      <div>Prêmio (20 acertos): <b>${formatarMoedaHist(s.premio)}</b></div>
      <div>Ganhadores: <b>${s.ganhadores != null ? s.ganhadores : '—'}</b></div>
      <div>🎯 Surpresas (0 pts): <b>${surpresas} aposta${surpresas === 1 ? '' : 's'} ganhadora${surpresas === 1 ? '' : 's'}</b></div>
    </div>`;
  return div;
}

function construirLinhaSorteio(s) {
  const row = document.createElement('div');
  row.className = 'hist-sorteio-row';
  row.dataset.concurso = s.concurso;
  row.dataset.dezenas = s.dezenas.join(',');
  const arrow = document.createElement('span');
  arrow.className = 'hist-arrow';
  arrow.textContent = '▶';
  row.appendChild(arrow);

  const texto = document.createElement('span');
  texto.innerHTML = `${s.data_br} ${s.acumulado ? '⭐' : ''} &nbsp;<span class="hist-concurso">Concurso ${s.concurso}</span>`;
  row.appendChild(texto);

  const detalhe = document.createElement('div');
  detalhe.className = 'hist-sorteio-detail';
  let construido = false;

  function abrir() {
    if (!construido) {
      detalhe.appendChild(construirDetalheSorteio(s));
      construido = true;
    }
    arrow.classList.add('open');
    expandirEl(detalhe);
  }
  function fechar() {
    arrow.classList.remove('open');
    colapsarEl(detalhe);
  }
  row.addEventListener('click', () => {
    arrow.classList.contains('open') ? fechar() : abrir();
  });
  row._abrirDetalheHist = abrir;

  return { row, detalhe };
}

function construirMes(anoKey, mesKey, mesNode, frag) {
  const mesDiv = document.createElement('div');
  mesDiv.className = 'hist-mes';
  mesDiv.dataset.ano = anoKey;
  mesDiv.dataset.mes = mesKey;

  const header = document.createElement('div');
  header.className = 'hist-mes-header';
  const arrow = document.createElement('span');
  arrow.className = 'hist-arrow';
  arrow.textContent = '▶';
  header.appendChild(arrow);
  const titulo = document.createElement('span');
  titulo.className = 'hist-titulo';
  titulo.textContent = mesNode.label;
  header.appendChild(titulo);
  const contador = document.createElement('span');
  contador.className = 'hist-contador';
  contador.textContent = `${mesNode.label} ${anoKey} — ${mesNode.total} sorteios`;
  header.appendChild(contador);
  mesDiv.appendChild(header);

  const sorteiosDiv = document.createElement('div');
  sorteiosDiv.className = 'hist-sorteios';
  const sorteiosFrag = document.createDocumentFragment();
  mesNode.sorteios.forEach(s => {
    const { row, detalhe } = construirLinhaSorteio(s);
    sorteiosFrag.appendChild(row);
    sorteiosFrag.appendChild(detalhe);
  });
  sorteiosDiv.appendChild(sorteiosFrag);
  mesDiv.appendChild(sorteiosDiv);

  header.addEventListener('click', () => {
    const aberto = arrow.classList.contains('open');
    arrow.classList.toggle('open', !aberto);
    aberto ? colapsarEl(sorteiosDiv) : expandirEl(sorteiosDiv);
  });

  frag.appendChild(mesDiv);
  return { arrow, sorteiosDiv };
}

function construirAno(anoKey, anoNode, raizFrag, expandidoPorPadrao) {
  const anoDiv = document.createElement('div');
  anoDiv.className = 'hist-ano';

  const header = document.createElement('div');
  header.className = 'hist-ano-header';
  const arrow = document.createElement('span');
  arrow.className = 'hist-arrow';
  arrow.textContent = '▶';
  header.appendChild(arrow);
  const titulo = document.createElement('span');
  titulo.className = 'hist-titulo';
  titulo.textContent = anoKey;
  header.appendChild(titulo);
  const contador = document.createElement('span');
  contador.className = 'hist-contador';
  contador.textContent = `${anoKey} — ${anoNode.total} sorteios`;
  header.appendChild(contador);
  anoDiv.appendChild(header);

  const mesesDiv = document.createElement('div');
  mesesDiv.className = 'hist-meses';
  const mesesFrag = document.createDocumentFragment();
  const mesesKeys = Object.keys(anoNode.meses).sort().reverse();
  const mesesInfo = mesesKeys.map(mesKey => construirMes(anoKey, mesKey, anoNode.meses[mesKey], mesesFrag));
  mesesDiv.appendChild(mesesFrag);
  anoDiv.appendChild(mesesDiv);

  header.addEventListener('click', () => {
    const aberto = arrow.classList.contains('open');
    arrow.classList.toggle('open', !aberto);
    aberto ? colapsarEl(mesesDiv) : expandirEl(mesesDiv);
  });

  raizFrag.appendChild(anoDiv);

  if (expandidoPorPadrao) {
    arrow.classList.add('open');
    mesesDiv.style.maxHeight = 'none';
    if (mesesInfo.length) {
      mesesInfo[0].arrow.classList.add('open');
      mesesInfo[0].sorteiosDiv.style.maxHeight = 'none';
    }
  }
}

// Meses sempre caem inteiros dentro (ou fora) de qualquer período do seletor,
// então dá pra filtrar a árvore comparando só ano+mês de cada .hist-mes.
function sorteioNoPeriodo(ano, mes, periodoId) {
  if (periodoId === '__todos__') return true;
  const m = periodoId.match(/^(\d{4})(?:-([A-Z])(\d+))?$/);
  if (!m) return false;
  const [, anoAlvo, tipo, numStr] = m;
  if (String(ano) !== anoAlvo) return false;
  if (!tipo) return true;
  const num = +numStr;
  if (tipo === 'S') return num === 1 ? mes <= 6 : mes >= 7;
  if (tipo === 'T') return Math.ceil(mes / 3) === num;
  if (tipo === 'B') return Math.ceil(mes / 2) === num;
  if (tipo === 'M') return mes === num;
  return false;
}

function aplicarFiltroPeriodoHistorico(periodoId) {
  if (!historicoInicializado) return;
  const raiz = document.getElementById('historico-arvore');
  raiz.querySelectorAll('.hist-mes').forEach(mesDiv => {
    const dentro = sorteioNoPeriodo(mesDiv.dataset.ano, +mesDiv.dataset.mes, periodoId);
    mesDiv.classList.toggle('hist-fora-periodo', !dentro);
  });
  raiz.querySelectorAll('.hist-ano').forEach(anoDiv => {
    const algumMesVisivel = [...anoDiv.querySelectorAll('.hist-mes')]
      .some(mesDiv => !mesDiv.classList.contains('hist-fora-periodo'));
    anoDiv.classList.toggle('hist-fora-periodo', !algumMesVisivel);
  });
}

function inicializarHistorico() {
  if (historicoInicializado) return;
  historicoInicializado = true;

  const h = DATA.historico;
  document.getElementById('hist-ultimo').textContent = `Concurso ${h.ultimo_concurso} — ${h.ultima_data}`;
  document.getElementById('hist-total').textContent = `${h.total} sorteios`;

  const raiz = document.getElementById('historico-arvore');
  const raizFrag = document.createDocumentFragment();
  const anosKeys = Object.keys(h.arvore).sort().reverse();
  anosKeys.forEach((anoKey, idx) => construirAno(anoKey, h.arvore[anoKey], raizFrag, idx === 0));
  raiz.appendChild(raizFrag);

  const btnExpandir = document.getElementById('hist-btn-expandir-tudo');
  btnExpandir.addEventListener('click', () => {
    const expandindo = btnExpandir.textContent === 'Expandir tudo';
    raiz.querySelectorAll('.hist-meses, .hist-sorteios').forEach(container => {
      const header = container.previousElementSibling;
      header.querySelector('.hist-arrow').classList.toggle('open', expandindo);
      container.style.maxHeight = expandindo ? 'none' : '0px';
    });
    btnExpandir.textContent = expandindo ? 'Colapsar tudo' : 'Expandir tudo';
  });

  function buscarConcurso(numero) {
    const erroEl = document.getElementById('hist-busca-erro');
    const row = raiz.querySelector(`.hist-sorteio-row[data-concurso="${numero}"]`);
    if (!row) {
      erroEl.textContent = `Concurso ${numero} não encontrado.`;
      erroEl.style.display = 'block';
      return;
    }
    erroEl.style.display = 'none';
    abrirContainer(row.closest('.hist-meses'));
    abrirContainer(row.closest('.hist-sorteios'));
    row._abrirDetalheHist();
    row.classList.add('hist-highlight');
    setTimeout(() => row.classList.remove('hist-highlight'), 1700);
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  document.getElementById('hist-btn-buscar').addEventListener('click', () => {
    const v = document.getElementById('hist-busca-concurso').value.trim();
    const num = parseInt(v, 10);
    if (Number.isInteger(num)) buscarConcurso(num);
  });
  document.getElementById('hist-busca-concurso').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') document.getElementById('hist-btn-buscar').click();
  });

  function filtrarPorDezena(numero) {
    raiz.querySelectorAll('.hist-sorteio-row').forEach(row => {
      const bate = row.dataset.dezenas.split(',').map(Number).includes(numero);
      row.classList.toggle('hist-match', bate);
      row.classList.toggle('hist-filtrado-fora', !bate);
    });
    raiz.querySelectorAll('.hist-mes').forEach(mesDiv => {
      if (mesDiv.querySelector('.hist-sorteio-row.hist-match')) {
        abrirContainer(mesDiv.closest('.hist-meses'));
        abrirContainer(mesDiv.querySelector('.hist-sorteios'));
      }
    });
  }
  function limparFiltroDezena() {
    raiz.querySelectorAll('.hist-sorteio-row').forEach(row => {
      row.classList.remove('hist-match', 'hist-filtrado-fora');
    });
  }

  document.getElementById('hist-filtro-dezena').addEventListener('input', (ev) => {
    const v = ev.target.value.trim();
    if (v === '') { limparFiltroDezena(); return; }
    const num = parseInt(v, 10);
    if (Number.isInteger(num) && num >= 0 && num <= 99) filtrarPorDezena(num);
  });
  document.getElementById('hist-btn-limpar-filtro').addEventListener('click', () => {
    document.getElementById('hist-filtro-dezena').value = '';
    limparFiltroDezena();
  });

  aplicarFiltroPeriodoHistorico(periodoAtualId);
}

// ── Verificação leve de sorteios novos + botão "Atualizar dados" ────────────
// Só aparece quando o HTML foi gerado com --source supabase. O token do
// GitHub é o MESMO localStorage key dos dashboards da Lotofácil e da
// Mega-Sena (mesma origem, mesmo repositório) — configura uma vez, funciona
// nos três dashboards.
const GH_TOKEN_KEY = 'gh_token';
function ghGetToken() { return localStorage.getItem(GH_TOKEN_KEY); }
function ghSetToken(t) { localStorage.setItem(GH_TOKEN_KEY, t); }
function ghRemoverToken() { localStorage.removeItem(GH_TOKEN_KEY); }

async function dispararWorkflow(token) {
  const repo = DATA.meta.github_repo;
  const workflow = DATA.meta.workflow_file;
  const res = await fetch(
    `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`,
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main' }),
    }
  );
  return res.status;
}

if (DATA.meta.supabase) {
  const wrap = document.getElementById('supabase-status');
  wrap.style.display = 'flex';

  const msgEl = document.createElement('span');
  msgEl.className = 'msg';
  const btnVerificar = document.createElement('button');
  btnVerificar.className = 'btn-secondary';
  btnVerificar.textContent = '🔍 Verificar novos sorteios';

  const repo = DATA.meta.github_repo;
  const dot = document.createElement('span');
  dot.className = 'gh-token-dot';
  const btnAtualizar = document.createElement('button');
  btnAtualizar.className = 'btn-primary';
  btnAtualizar.textContent = '🔄 Atualizar dados';
  btnAtualizar.title = repo
    ? 'Dispara o workflow do GitHub Actions'
    : 'Configure --github-repo ao gerar o dashboard para habilitar este botão';
  btnAtualizar.disabled = !repo;
  if (!repo) btnAtualizar.style.opacity = '0.5';

  const btnGear = document.createElement('button');
  btnGear.className = 'gh-gear-btn';
  btnGear.textContent = '⚙️';
  btnGear.title = 'Configurar token do GitHub';
  btnGear.disabled = !repo;

  const feedbackEl = document.getElementById('gh-feedback');
  const bannerEl = document.getElementById('update-banner');

  const overlay = document.getElementById('gh-modal-overlay');
  const inputToken = document.getElementById('gh-token-input');
  const btnToggleVisibility = document.getElementById('gh-token-toggle');
  const modalErro = document.getElementById('gh-modal-erro');
  const btnModalSalvar = document.getElementById('gh-modal-salvar');
  const btnModalCancelar = document.getElementById('gh-modal-cancelar');
  const btnModalRemover = document.getElementById('gh-modal-remover');

  function atualizarDot() {
    dot.classList.toggle('ativo', !!ghGetToken());
  }

  function mostrarFeedback(texto, tipo) {
    feedbackEl.textContent = texto;
    feedbackEl.className = 'gh-feedback ' + tipo;
    feedbackEl.style.display = 'block';
  }

  function abrirModal() {
    modalErro.style.display = 'none';
    inputToken.value = '';
    inputToken.type = 'password';
    btnModalRemover.style.display = ghGetToken() ? 'inline-block' : 'none';
    overlay.style.display = 'flex';
  }
  function fecharModal() {
    overlay.style.display = 'none';
  }

  btnToggleVisibility.addEventListener('click', () => {
    inputToken.type = inputToken.type === 'password' ? 'text' : 'password';
  });
  btnModalCancelar.addEventListener('click', fecharModal);
  btnModalRemover.addEventListener('click', () => {
    ghRemoverToken();
    atualizarDot();
    fecharModal();
  });
  btnModalSalvar.addEventListener('click', () => {
    const val = inputToken.value.trim();
    if (!val) {
      modalErro.textContent = 'Cole um token válido.';
      modalErro.style.display = 'block';
      return;
    }
    ghSetToken(val);
    atualizarDot();
    fecharModal();
    acionarAtualizacao();
  });
  btnGear.addEventListener('click', abrirModal);

  async function acionarAtualizacao() {
    if (!repo) return;
    const token = ghGetToken();
    if (!token) { abrirModal(); return; }

    feedbackEl.style.display = 'none';
    btnAtualizar.disabled = true;
    btnAtualizar.textContent = '⏳ Disparando...';
    try {
      const status = await dispararWorkflow(token);
      if (status === 204) {
        btnAtualizar.textContent = '✓ Atualização iniciada!';
        mostrarFeedback('O GitHub Actions está rodando. Aguarde ~2 minutos e recarregue a página para ver os dados novos.', 'ok');
      } else if (status === 401) {
        btnAtualizar.textContent = '❌ Token inválido';
        mostrarFeedback('Token inválido ou expirado. Clique em ⚙️ para configurar um novo.', 'erro');
      } else {
        btnAtualizar.textContent = '❌ Erro';
        mostrarFeedback(`Erro ao disparar o workflow (código ${status}).`, 'erro');
      }
    } catch (e) {
      btnAtualizar.textContent = '❌ Erro';
      mostrarFeedback('Erro de rede ao chamar a API do GitHub: ' + e.message, 'erro');
    } finally {
      btnAtualizar.disabled = false;
      setTimeout(() => { btnAtualizar.textContent = '🔄 Atualizar dados'; }, 5000);
    }
  }

  async function verificarNovosSorteios() {
    msgEl.textContent = 'Verificando…';
    msgEl.className = 'msg';
    const { url, anon_key } = DATA.meta.supabase;
    const tabela = DATA.meta.tabela;
    try {
      const res = await fetch(
        `${url}/rest/v1/${tabela}?select=concurso,data_br&order=concurso.desc&limit=1`,
        { headers: { apikey: anon_key, Authorization: `Bearer ${anon_key}` } }
      );
      const dados = await res.json();
      const agora = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
      if (!res.ok || !dados || !dados.length) {
        msgEl.textContent = `Não foi possível consultar o Supabase (verificado às ${agora})`;
        bannerEl.style.display = 'none';
        return;
      }
      const ultimoRemoto = +dados[0].concurso;
      const ultimoAtual = +DATA.meta.concurso_fim;
      if (ultimoRemoto > ultimoAtual) {
        msgEl.textContent = `Novo concurso disponível: ${ultimoRemoto} (${dados[0].data_br})`;
        msgEl.className = 'msg novo';

        bannerEl.innerHTML = '';
        const texto = document.createElement('span');
        texto.textContent = `⚠️ Existe um sorteio mais recente disponível (concurso ${ultimoRemoto}, ${dados[0].data_br}). Clique em Atualizar para buscar.`;
        const btnBanner = document.createElement('button');
        btnBanner.textContent = '🔄 Atualizar agora';
        btnBanner.disabled = !repo;
        btnBanner.addEventListener('click', acionarAtualizacao);
        bannerEl.appendChild(texto);
        bannerEl.appendChild(btnBanner);
        bannerEl.style.display = 'flex';
      } else {
        msgEl.textContent = `Atualizado em ${agora} — nenhum concurso novo`;
        msgEl.className = 'msg atualizado';
        bannerEl.style.display = 'none';
      }
    } catch (e) {
      msgEl.textContent = 'Erro ao verificar: ' + e.message;
      bannerEl.style.display = 'none';
    }
  }

  btnVerificar.addEventListener('click', verificarNovosSorteios);
  btnAtualizar.addEventListener('click', acionarAtualizacao);
  atualizarDot();
  wrap.appendChild(msgEl);
  wrap.appendChild(btnVerificar);
  wrap.appendChild(dot);
  wrap.appendChild(btnAtualizar);
  wrap.appendChild(btnGear);
  verificarNovosSorteios();
}
</script>
</body>
</html>
"""


def gerar_html(rows: list[dict], output: str, fonte_supabase: dict | None = None, github_repo: str | None = None):
    sorteios = [dezenas(r) for r in rows]
    n = len(sorteios)

    freq = calc_frequencia(sorteios)
    atraso = calc_atraso(sorteios)
    pi = calc_pares_impares(sorteios)
    faixas = calc_faixas(sorteios)
    somas = calc_soma(sorteios)
    cooc = calc_coocorrencia(sorteios, top_n=20)
    dist_tam, top_por_tam = calc_sequencias_consecutivas(sorteios)
    tendencia = calc_tendencia(sorteios, janela=50)
    repeticao_anterior = calc_repeticao_anterior(sorteios)
    ciclo_medio = calc_ciclo_medio(sorteios)
    cooc_completo = calc_coocorrencia_completa(sorteios)
    anticorrelacao = calc_anticorrelacao(cooc_completo, bottom_n=15)

    blocos = calc_blocos_bundle(rows, sorteios)
    blocos_periodo = calc_blocos_periodo(rows, sorteios)
    periodos, periodos_disponiveis = gerar_periodos(rows, sorteios)
    historico = calc_historico(rows, sorteios)
    financeiro = calc_financeiro(rows)
    faixa_surpresa = calc_faixa_surpresa(rows)
    jogos = calc_jogos_lotomania(JOGOS_LOTOMANIA, rows, sorteios) if JOGOS_LOTOMANIA else None
    repeticoes = detectar_repeticoes_lotomania(rows)
    prob_repeticao_pct = prob_repeticao(n, TOTAL_COMBINACOES)

    data = {
        "meta": {
            "total": n,
            "inicio": rows[0]["data"],
            "fim": rows[-1]["data"],
            "concurso_ini": rows[0]["concurso"],
            "concurso_fim": rows[-1]["concurso"],
            "supabase": fonte_supabase,
            "github_repo": github_repo,
            "tabela": "lotomania_sorteios",
            "workflow_file": "lotomania_atualizar.yml",
        },
        "frequencia": {d: c for d, c in freq.items()},
        "atraso": atraso,
        "pares_impares": pi,
        "faixas": faixas,
        "somas": somas,
        "coocorrencia": [[[a, b], c] for (a, b), c in cooc],
        "seq_dist_tamanho": {str(k): v for k, v in sorted(dist_tam.items())},
        "seq_top_por_tamanho": {str(k): v for k, v in top_por_tam.items()},
        "tendencia": tendencia,
        "repeticao_anterior": repeticao_anterior,
        "ciclo_medio": ciclo_medio,
        "anticorrelacao": [[[a, b], c] for (a, b), c in anticorrelacao],
        "sorteios_raw": sorteios,
        "sorteios_meta": [_meta_sorteio(r) for r in rows],
        "blocos": blocos,
        "blocos_periodo": blocos_periodo,
        "periodos": periodos,
        "periodos_disponiveis": periodos_disponiveis,
        "historico": historico,
        "financeiro": financeiro,
        "faixa_surpresa": faixa_surpresa,
        "jogos": jogos,
        "jogos_config": JOGOS_LOTOMANIA or {},
        "repeticoes": repeticoes,
        "prob_repeticao_pct": prob_repeticao_pct,
        "total_combinacoes": TOTAL_COMBINACOES,
    }

    from datetime import datetime
    titulo = f"{n} sorteios — concursos {rows[0]['concurso']} a {rows[-1]['concurso']}"
    subtitulo = f"Concurso {rows[-1]['concurso']} · {rows[-1]['data']} · {n} sorteios analisados"
    gerado_em = f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}"

    html = (HTML_TEMPLATE
            .replace("{titulo}", titulo)
            .replace("{subtitulo}", subtitulo)
            .replace("{gerado_em}", gerado_em)
            .replace("{data_json}", json.dumps(data, ensure_ascii=False)))

    with open(output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✓ Dashboard salvo em '{output}'  ({n} sorteios)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera BI HTML dos sorteios da Lotomania")
    parser.add_argument("--db", default="lotomania.db", help="Caminho do banco SQLite (lotomania.db)")
    parser.add_argument("--source", choices=["supabase", "local"], default="supabase",
                         help="'supabase' lê direto do Supabase; 'local' lê do SQLite em --db")
    parser.add_argument("--supabase-url", default=None)
    parser.add_argument("--supabase-key", default=None, help="SUPABASE_ANON_KEY (leitura pública)")
    parser.add_argument("--periodo", default=None, help="Filtra por ano (ex: 2025) ou prefixo ISO (ex: 2025-06)")
    parser.add_argument("--output", default="lotomania.html", help="Arquivo HTML de saída")
    parser.add_argument("--github-repo", default="andrevisc-1209/lotofacil-bi",
                         help="dono/repositorio — usado só pelo botão 'Atualizar dados'")
    args = parser.parse_args()

    fonte_supabase = None

    if args.source == "supabase":
        import lotomania_db
        url = args.supabase_url
        key = args.supabase_key
        if not (url and key):
            env_url, env_key = lotomania_db.carregar_credenciais_supabase()
            url = url or env_url
            key = key or env_key
        if not (url and key):
            print("SUPABASE_URL e SUPABASE_ANON_KEY precisam estar configurados "
                  "(--supabase-url/--supabase-key, variável de ambiente ou .env).")
            exit(1)
        db = lotomania_db.Database.supabase(url, key)
        rows = carregar_de_database(db)
        db.fechar()
        print(f"Carregados {len(rows)} sorteios do Supabase.")
        fonte_supabase = {"url": url, "anon_key": key}
    else:  # local
        if not Path(args.db).exists():
            print(f"Banco '{args.db}' não encontrado. Rode primeiro: python lotomania_atualizar.py --init-all")
            exit(1)
        import lotomania_db
        db = lotomania_db.Database.sqlite(args.db)
        rows = carregar_de_database(db)
        db.fechar()
        print(f"Carregados {len(rows)} sorteios de '{args.db}'.")

    if not rows:
        print("Nenhum sorteio encontrado na fonte de dados. Rode lotomania_atualizar.py primeiro.")
        exit(1)

    if args.periodo:
        rows = filtrar_por_periodo(rows, args.periodo)
        print(f"Filtrado para o período '{args.periodo}': {len(rows)} sorteios.")
        if not rows:
            print("Nenhum sorteio encontrado para esse período.")
            exit(1)

    gerar_html(rows, args.output, fonte_supabase=fonte_supabase, github_repo=args.github_repo)

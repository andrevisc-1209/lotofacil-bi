"""
duplasena_bi.py
-----------------
Gera o dashboard HTML interativo da Dupla Sena (6 dezenas de 01-50, DOIS
sorteios independentes por concurso — 1ª e 2ª rodada) — mesmo padrão visual e
arquitetural de megasena_bi.py (Python pré-computa tudo por período, JS só
renderiza), com 3 abas (Análise Geral / Blocos / Histórico) e SEM simulador de
aposta nem validador "Meus jogos" (decisão de escopo deliberada, igual à nota
do cabeçalho de megasena_bi.py pra esse dashboard não fazer sentido aqui).

Uso:
    python duplasena_bi.py --db duplasena.db --source local
    python duplasena_bi.py --source supabase
    python duplasena_bi.py --output duplasena.html
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from math import comb, exp
from pathlib import Path

UNIVERSO_MIN, UNIVERSO_MAX = 1, 50  # confirmado via API real: "01".."50", sem "00"


# ─── carrega dados do banco (SQLite ou Supabase, via duplasena_db.Database) ───

def carregar_de_database(db) -> list[dict]:
    """Mesma lógica de megasena_bi.carregar_de_database: linhas com 'data' em
    formato BR (igual ao que a UI espera) e 'data_iso' extra pro bucketing
    temporal. Preserva d01-d06/s01-s06 e as 16 colunas de prêmio intactas."""
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

def dezenas_r1(row: dict) -> list[int]:
    return sorted(int(row[f"d{i:02d}"]) for i in range(1, 7))

def dezenas_r2(row: dict) -> list[int]:
    return sorted(int(row[f"s{i:02d}"]) for i in range(1, 7))

def interleave_rodadas(r1_list: list, r2_list: list) -> list:
    """Funde as duas rodadas numa única linha do tempo (r1 do concurso N, r2
    do concurso N, r1 do concurso N+1, ...) — essa é a lista "sorteios"
    usada por toda a análise combinada (frequência, blocos, co-ocorrência
    etc.), já que cada concurso produz 2 sorteios independentes do mesmo
    universo. Convenção usada em todo o arquivo: 1 concurso = 2 "sorteios"."""
    pool = []
    for a, b in zip(r1_list, r2_list):
        pool.append(a)
        pool.append(b)
    return pool


# ─── análises (universo 01-50, 6 dezenas por sorteio) ─────────────────────────

def calc_frequencia(sorteios):
    c = Counter()
    for s in sorteios:
        c.update(s)
    return c

def calc_pares_impares(sorteios):
    resultados = []
    for s in sorteios:
        p = sum(1 for d in s if d % 2 == 0)
        resultados.append({"pares": p, "impares": 6 - p})
    return resultados

def calc_faixas(sorteios):
    """Distribuição por faixa em cada sorteio — terços do universo 1-50
    (17/17/16 dezenas)."""
    resultados = []
    for s in sorteios:
        resultados.append({
            "baixo": sum(1 for d in s if 1 <= d <= 17),
            "medio": sum(1 for d in s if 18 <= d <= 34),
            "alto":  sum(1 for d in s if 35 <= d <= 50),
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
    itens = sorted(cooc_completo.items(), key=lambda kv: kv[1])
    return itens[:bottom_n]

def calc_sequencias_consecutivas(sorteios):
    """Runs de números consecutivos dentro de cada sorteio de 6 dezenas
    (universo-agnóstico — mesma lógica de megasena_bi)."""
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


# ─── comparativo 1ª vs 2ª rodada — a identidade da Dupla Sena ─────────────────

def calc_comparativo_rodadas(r1_p: list, r2_p: list) -> dict:
    """Frequência separada por rodada (pra revelar viés de uma rodada sobre a
    outra) + quantas dezenas se repetem entre as 2 rodadas do MESMO concurso,
    comparado à expectativa teórica de sorteios independentes (6×6/50=0.72).
    Como as rodadas são de fato independentes, esse último número é só uma
    checagem de sanidade estatística, não um padrão a ser garimpado."""
    freq_r1 = calc_frequencia(r1_p)
    freq_r2 = calc_frequencia(r2_p)
    linhas = []
    for d in range(UNIVERSO_MIN, UNIVERSO_MAX + 1):
        f1, f2 = freq_r1.get(d, 0), freq_r2.get(d, 0)
        linhas.append({"d": d, "r1": f1, "r2": f2, "delta": f1 - f2})
    skew_r1 = sorted(linhas, key=lambda x: x["delta"], reverse=True)[:10]
    skew_r2 = sorted(linhas, key=lambda x: x["delta"])[:10]
    top_r1 = max(freq_r1.items(), key=lambda kv: kv[1], default=(None, 0))
    top_r2 = max(freq_r2.items(), key=lambda kv: kv[1], default=(None, 0))

    intersecoes = [len(set(a) & set(b)) for a, b in zip(r1_p, r2_p)]
    media_intersecao = round(sum(intersecoes) / len(intersecoes), 3) if intersecoes else 0.0
    dist_intersecoes = Counter(intersecoes)

    return {
        "freq_r1": {d: c for d, c in freq_r1.items()},
        "freq_r2": {d: c for d, c in freq_r2.items()},
        "top_r1": {"d": top_r1[0], "c": top_r1[1]},
        "top_r2": {"d": top_r2[0], "c": top_r2[1]},
        "skew_r1": skew_r1,
        "skew_r2": skew_r2,
        "media_intersecao": media_intersecao,
        "expectativa_teorica": round(6 * 6 / 50, 2),
        "dist_intersecoes": {str(k): v for k, v in sorted(dist_intersecoes.items())},
    }


# ─── detectores de curiosidade estatística ─────────────────────────────────────

TOTAL_COMBINACOES = comb(50, 6)  # ≈ 15.900.700 — universo de resultados possíveis

def detectar_rodadas_identicas(rows: list[dict]) -> list[dict]:
    """Concursos em que a 1ª e a 2ª rodada sortearam exatamente as mesmas 6
    dezenas — checagem DENTRO do mesmo concurso (não confundir com o detector
    de sorteios repetidos ENTRE concursos, abaixo). Astronomicamente
    improvável (comb(50,6) combinações possíveis)."""
    achados = []
    for r in rows:
        r1 = tuple(dezenas_r1(r))
        r2 = tuple(dezenas_r2(r))
        if r1 == r2:
            achados.append({
                "concurso": _to_int(r["concurso"]),
                "data": r.get("data_br") or r["data"],
                "numeros": list(r1),
            })
    return achados

def detectar_repeticoes(rows: list[dict]) -> list[dict]:
    """Sorteios com as mesmas 6 dezenas ENTRE concursos diferentes. Agrupa
    todos os sorteios (1ª E 2ª rodada de todo o histórico) numa única
    contagem por combinação de números — mais simples que manter dois
    detectores separados, e cobre de graça os 3 casos possíveis (repetição
    dentro do histórico da rodada 1, dentro da rodada 2, ou cruzada entre
    rodadas de concursos diferentes), já que a chave de agrupamento é só o
    conjunto de números — a rodada de origem vai só na metadata de exibição."""
    grupos = defaultdict(list)
    for r in rows:
        for rodada_nome, dezenas_fn in (("1ª rodada", dezenas_r1), ("2ª rodada", dezenas_r2)):
            chave = tuple(dezenas_fn(r))
            grupos[chave].append({
                "concurso": _to_int(r["concurso"]),
                "data": r.get("data_br") or r["data"],
                "rodada": rodada_nome,
            })

    repeticoes = []
    for numeros, sorteios in grupos.items():
        if len(sorteios) > 1:
            repeticoes.append({
                "numeros": list(numeros),
                "vezes": len(sorteios),
                "sorteios": sorted(sorteios, key=lambda x: (x["concurso"], x["rodada"])),
            })
    return sorted(repeticoes, key=lambda x: x["vezes"], reverse=True)

def prob_repeticao(n_sorteios: int, n_combinacoes: int) -> float:
    """Probabilidade aproximada (problema do aniversário) de pelo menos 1
    repetição depois de n_sorteios sorteios."""
    if n_sorteios < 2:
        return 0.0
    prob = 1 - exp(-n_sorteios * (n_sorteios - 1) / (2 * n_combinacoes))
    return round(prob * 100, 4)


# ─── blocos de 10 (A: 01-10, B: 11-20, C: 21-30, D: 31-40, E: 41-50) ──────────

BLOCOS_NOMES = ["A", "B", "C", "D", "E"]

def bloco_de(d: int) -> int:
    return (d - 1) // 10

def _contagem_blocos(s) -> list:
    c = Counter(bloco_de(d) for d in s)
    return [c.get(i, 0) for i in range(5)]

def calc_blocos_freq_individual(sorteios):
    freq = calc_frequencia(sorteios)
    resultado = {}
    for i, nome in enumerate(BLOCOS_NOMES):
        inicio = i * 10 + 1
        resultado[nome] = {d: freq.get(d, 0) for d in range(inicio, inicio + 10)}
    return resultado

def calc_blocos_combinacoes(sorteios, top_n=15):
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
    """Matriz 5×5: frequência com que cada par de blocos contribui com >=2
    dezenas no mesmo sorteio (cada bloco só tem espaço pra até 6 dezenas de
    um sorteio de 6, então >=2 já é acima da média esperada de 6/5=1.2)."""
    matriz = [[0] * 5 for _ in range(5)]
    for s in sorteios:
        c = _contagem_blocos(s)
        ativos = [i for i in range(5) if c[i] >= 2]
        for i in ativos:
            matriz[i][i] += 1
            for j in ativos:
                if i != j:
                    matriz[i][j] += 1
    return matriz

def calc_blocos_bundle(sorteios):
    return {
        "freq_individual": calc_blocos_freq_individual(sorteios),
        "combinacoes": calc_blocos_combinacoes(sorteios, top_n=15),
        "coocorrencia": calc_blocos_coocorrencia(sorteios),
    }

def calc_blocos_periodo(rows):
    """Média de dezenas por bloco, agrupado por mês (YYYY-MM) — pool das duas
    rodadas, histórico completo (não filtra por período do seletor)."""
    buckets = defaultdict(list)
    for row in rows:
        periodo = data_iso_de(row)[:7]
        buckets[periodo].append(_contagem_blocos(dezenas_r1(row)))
        buckets[periodo].append(_contagem_blocos(dezenas_r2(row)))
    resultado = []
    for periodo in sorted(buckets):
        valores = buckets[periodo]
        medias = [round(sum(v[i] for v in valores) / len(valores), 2) for i in range(5)]
        resultado.append({"periodo": periodo, "medias": medias, "total": len(valores)})
    return resultado

def calc_blocos_rodada_nota(r1_p: list, r2_p: list) -> dict:
    """Média de dezenas por bloco em cada rodada, separadamente — nota de
    consistência pra Aba Blocos (devem ficar parecidas entre si, já que são
    sorteios independentes do mesmo universo; não vira uma UI paralela
    completa, só uma tabelinha de checagem)."""
    def medias(sorteios):
        n = len(sorteios)
        if not n:
            return [0.0] * 5
        soma = [0] * 5
        for s in sorteios:
            for i, c in enumerate(_contagem_blocos(s)):
                soma[i] += c
        return [round(v / n, 2) for v in soma]
    return {"r1": medias(r1_p), "r2": medias(r2_p)}


# ─── resumo financeiro (faixa "sena" = topo de cada rodada, sempre existiu) ───

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

def calc_financeiro(rows_p: list[dict]) -> dict:
    """sena1/sena2 (faixa 6 acertos de cada rodada) existem desde o concurso
    1 — diferente de quina1/quadra1/terno1/terno2, que só passaram a existir
    bem mais tarde (ver duplasena_db.NOME_FAIXA_POR_NUMERO). Ainda assim
    filtra None por segurança/consistência com o padrão do projeto."""
    def stats(campo):
        vals = [(_to_float(r.get(campo)), r) for r in rows_p]
        vals = [(v, r) for v, r in vals if v is not None]
        if not vals:
            return {"media": None, "maior": None}
        media = round(sum(v for v, _ in vals) / len(vals), 2)
        maior_v, maior_r = max(vals, key=lambda x: x[0])
        return {
            "media": media,
            "maior": {"valor": maior_v, "concurso": _to_int(maior_r["concurso"]), "data": maior_r.get("data")},
        }

    n = len(rows_p)
    total_acumulados = sum(1 for r in rows_p if _acumulado_bool(r.get("acumulado")))
    return {
        "total_concursos": n,
        "total_sorteios": n * 2,
        "sena1": stats("valor_sena1"),
        "sena2": stats("valor_sena2"),
        "total_acumulados": total_acumulados,
        "pct_acumulados": round(total_acumulados / n * 100, 1) if n else 0,
    }

def _meta_sorteio_rodada(row: dict, rodada: int) -> dict:
    """Metadata de UM sorteio (uma rodada de um concurso) — usada em
    sorteios_meta, alinhada 1:1 com sorteios_raw (que também tem 2 entradas
    por concurso). Isso permite ao JS zipar sorteios_raw[i]/sorteios_meta[i]
    igual megasena_bi faz, sem se preocupar com o descompasso de tamanho que
    existiria se sorteios_meta ficasse em granularidade de concurso.
    Inclui valor_sena/quina/quadra/terno da rodada — usado pelo Simulador de
    Jogo (bolinhas) pra calcular ganho real por faixa (3/4/5/6 acertos)."""
    return {
        "concurso": _to_int(row["concurso"]),
        "data": row["data"],
        "rodada": rodada,
        "acumulado": _acumulado_bool(row.get("acumulado")),
        "valor_sena": _to_float(row.get(f"valor_sena{rodada}")),
        "valor_quina": _to_float(row.get(f"valor_quina{rodada}")),
        "valor_quadra": _to_float(row.get(f"valor_quadra{rodada}")),
        "valor_terno": _to_float(row.get(f"valor_terno{rodada}")),
    }


# ─── bundle de um período (Todos ou um recorte do seletor) ────────────────────

def calcular_bundle_periodo(rows_p: list[dict]) -> dict:
    r1_p = [dezenas_r1(r) for r in rows_p]
    r2_p = [dezenas_r2(r) for r in rows_p]
    pool_p = interleave_rodadas(r1_p, r2_p)
    n = len(pool_p)

    freq = calc_frequencia(pool_p)
    pi = calc_pares_impares(pool_p)
    faixas = calc_faixas(pool_p)
    somas = calc_soma(pool_p)
    dist_tam, top_por_tam = calc_sequencias_consecutivas(pool_p)
    cooc = calc_coocorrencia(pool_p, top_n=20)
    cooc_completo_p = calc_coocorrencia_completa(pool_p)
    anticorrelacao = calc_anticorrelacao(cooc_completo_p, bottom_n=15)
    comparativo = calc_comparativo_rodadas(r1_p, r2_p)
    top_combinado = max(freq.items(), key=lambda kv: kv[1], default=(None, 0))

    sorteios_meta = []
    for r in rows_p:
        sorteios_meta.append(_meta_sorteio_rodada(r, 1))
        sorteios_meta.append(_meta_sorteio_rodada(r, 2))

    return {
        "meta": {
            "total": n,
            "total_concursos": len(rows_p),
            "inicio": rows_p[0]["data"] if rows_p else None,
            "fim": rows_p[-1]["data"] if rows_p else None,
        },
        "frequencia": {d: c for d, c in freq.items()},
        "top_combinado": {"d": top_combinado[0], "c": top_combinado[1]},
        "pares_impares": pi,
        "faixas": faixas,
        "somas": somas,
        "seq_dist_tamanho": {str(k): v for k, v in sorted(dist_tam.items())},
        "seq_top_por_tamanho": {str(k): v for k, v in top_por_tam.items()},
        "coocorrencia": [[[a, b], c] for (a, b), c in cooc],
        "anticorrelacao": [[[a, b], c] for (a, b), c in anticorrelacao],
        "comparativo_rodadas": comparativo,
        "blocos": calc_blocos_bundle(pool_p),
        "blocos_rodada_nota": calc_blocos_rodada_nota(r1_p, r2_p),
        "financeiro": calc_financeiro(rows_p),
        "sorteios_raw": pool_p,
        "sorteios_meta": sorteios_meta,
    }


# ─── seletor de período temporal (ano / semestre / trimestre / bimestre / mês) ─

MESES_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

def gerar_periodos(rows):
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
        periodos[pid] = calcular_bundle_periodo(rows_p)
        disponiveis.append({"id": pid, "label": labels[pid], "tipo": tipos[pid], "total": len(indices)})

    disponiveis.sort(key=lambda d: d["id"])
    return periodos, disponiveis


# ─── histórico em árvore (Ano → Mês → Concurso, cada card mostra as 2 rodadas) ─

DIAS_SEMANA_PT = {
    0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira", 3: "Quinta-feira",
    4: "Sexta-feira", 5: "Sábado", 6: "Domingo",
}
MESES_COMPLETOS_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

def calc_historico(rows: list[dict]) -> dict:
    from datetime import datetime

    arvore: dict = {}
    for row in rows:
        iso = data_iso_de(row)
        ano, mes = iso[:4], iso[5:7]
        dia_semana = DIAS_SEMANA_PT[datetime.strptime(iso, "%Y-%m-%d").weekday()]
        r1 = dezenas_r1(row)
        r2 = dezenas_r2(row)

        sorteio_info = {
            "concurso": _to_int(row["concurso"]),
            "data_iso": iso,
            "data_br": row["data"],
            "dia_semana": dia_semana,
            "r1": r1,
            "r2": r2,
            "todas_dezenas": sorted(set(r1) | set(r2)),  # filtro por dezena casa com QUALQUER rodada
            "acumulado": _acumulado_bool(row.get("acumulado")),
            "sena1_valor": _to_float(row.get("valor_sena1")),
            "sena1_ganhadores": _to_int(row.get("ganhadores_sena1")),
            "sena2_valor": _to_float(row.get("valor_sena2")),
            "sena2_ganhadores": _to_int(row.get("ganhadores_sena2")),
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
        "total_sorteios": len(rows) * 2,
        "arvore": arvore,
    }


# ─── geração do HTML ──────────────────────────────────────────────────────────
# Mesma paleta/arquitetura visual "Dark Analytics App" de megasena_bi.py — CSS
# quase idêntico, recolorido pro acento crimson (#e11d48/#fb7185) da Dupla Sena
# e com numgrid/blocos-grid ajustados pra 50 números em 5 blocos de 10, em vez
# de 60 números em 6 blocos. SEM simulador de aposta / "Meus jogos" (decisão de
# escopo documentada no cabeçalho deste arquivo) — no lugar, a Aba Geral ganha
# um card dedicado ao comparativo 1ª x 2ª rodada, a identidade única da Dupla
# Sena, alimentado por calc_comparativo_rodadas.

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Dupla Sena BI — {titulo}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    /* base surface tokens */
    --bg0: #0a0a0f;
    --bg1: #111118;
    --bg2: #0f1018;
    --bg3: #161824;
    --text: #e8e8f0;
    --text-2: #a0a0b8;
    --text-3: #606078;
    --border: #2a2a3a;
    --border-2: #3a3a4f;
    --shadow: 0 4px 24px rgba(0,0,0,0.4);
    --font: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
    --r-sm: 6px; --r-md: 10px; --r-lg: 14px; --r-xl: 20px;

    /* Dupla Sena accent — crimson (this dashboard's identity color) */
    --accent: #e11d48;
    --accent2: #fb7185;
    --accent3-crimson: #9f1239;
    --neon: rgba(225,29,72,0.15);

    /* status colors */
    --green: #22c55e;
    --red: #ef4444;
    --gold: #f59e0b;
    --yellow: #f59e0b;
    --blue: #3b82f6;

    /* back-compat aliases */
    --bg: var(--bg0);
    --card: var(--bg1);
    --muted: var(--text-3);
    --accent3: var(--green);
    --accent4: var(--gold);
    --accent5: var(--red);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    background: radial-gradient(ellipse 1200px 800px at 15% -10%, #3d0f1f 0%, transparent 60%),
                radial-gradient(ellipse 900px 700px at 100% 0%, #2b1030 0%, transparent 55%),
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
  .btn-primary:hover:not(:disabled) { background: var(--accent3-crimson); box-shadow: 0 0 0 3px rgba(225,29,72,.25); }
  .btn-primary:active:not(:disabled) { transform: scale(.97); }
  .btn-secondary { background: transparent; border: 1px solid var(--accent); color: var(--accent2); }
  .btn-secondary:hover:not(:disabled) { background: rgba(225,29,72,.12); }
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
  #gh-modal-salvar:hover { background: var(--accent3-crimson); }
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
  .card:hover { border-color: rgba(225,29,72,.3); box-shadow: var(--shadow), 0 0 0 1px rgba(225,29,72,.08); }
  .card h2 { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-3); margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
  @media (max-width: 640px) { .card { padding: 16px; border-radius: 12px; } }
  .page-content { animation: pageFadeIn .25s ease; }
  @keyframes pageFadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
  canvas { max-height: 280px; }
  .heatcell { border-radius: 10px; padding: 14px 4px; text-align: center; font-weight: 800; font-size: 16px; font-family: var(--font-mono); transition: transform .15s; cursor: default; }
  .heatcell:hover { transform: scale(1.12); z-index: 2; position: relative; }
  .heatcell .freq { font-size: 11px; font-weight: 500; opacity: .8; display: block; margin-top: 3px; }
  @media (max-width: 640px) {
    .heatcell { padding: 10px 2px; font-size: 13px; border-radius: 8px; }
    .heatcell .freq { font-size: 9px; }
  }
  /* grade interativa 5×10 (50 números) — substitui o mapa de calor estático */
  .numgrid { display: grid; grid-template-columns: repeat(10, 1fr); gap: 8px; max-width: 620px; margin: 0 auto; }
  .numgrid-cell {
    aspect-ratio: 1; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 12px; font-family: var(--font-mono); color: #fff; cursor: pointer; border: 2px solid transparent;
    transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
  }
  .numgrid-cell:hover { transform: scale(1.1); box-shadow: 0 4px 16px rgba(225,29,72,0.3); }
  .numgrid-cell.selecionada { border-color: var(--accent2); box-shadow: 0 0 0 4px var(--neon), 0 0 18px rgba(251,113,133,.55); transform: scale(1.06); }
  .numgrid-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 18px; flex-wrap: wrap; }
  .numgrid-hint { font-size: 12px; color: var(--muted); flex: 1 1 260px; }
  @media (max-width: 640px) {
    .numgrid { max-width: 340px; gap: 5px; }
    .numgrid-cell { font-size: 10px; }
    .numgrid-footer { flex-direction: column; align-items: stretch; }
  }
  /* simulador de jogo — seleção visual por bolinhas */
  .simball-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(38px, 1fr)); gap: 8px; margin-bottom: 16px; }
  .simball-cell {
    aspect-ratio: 1; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 13px; font-family: var(--font-mono); color: var(--text-2);
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
  .sim-error { color: var(--red); font-size: 12px; margin: -6px 0 12px; }
  .sim-detalhe-toggle { cursor: pointer; color: var(--accent2); font-size: 11px; background: none; border: none; padding: 0; text-decoration: underline; }
  .sim-detalhe-painel { display: none; padding: 10px 0 4px; }
  .sim-detalhe-painel.aberto { display: block; }
  .sim-historico { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; max-height: 200px; overflow-y: auto; }
  .sim-hist-item { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--r-sm); padding: 4px 10px; font-size: 11px; font-family: var(--font-mono); color: var(--text-2); white-space: nowrap; }
  .sim-hist-item span.concurso { color: var(--accent2); font-weight: 600; }
  .sim-hist-mais { margin-top: 8px; }
  .dsim-rodada-titulo { font-size: 12px; font-weight: 700; color: var(--text-3); text-transform: uppercase; letter-spacing: .06em; margin: 14px 0 8px; }
  .dsim-rodada-titulo:first-of-type { margin-top: 0; }
  .tabs { display: flex; gap: 0; margin-bottom: 14px; flex-wrap: wrap; border-bottom: 1px solid var(--border); }
  .tab { padding: 7px 16px; border-radius: 0; border: none; border-bottom: 2px solid transparent; background: transparent; color: var(--muted); cursor: pointer; font-size: 12px; font-weight: 600; transition: color .15s, border-bottom-color .15s; margin-bottom: -1px; }
  .tab.active { background: transparent; border-bottom-color: var(--accent); color: var(--text); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { padding: 10px 14px; text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-3); font-weight: 600; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--card); z-index: 1; }
  td { padding: 10px 14px; color: var(--text-2); font-family: var(--font-mono); font-size: 12px; border-bottom: 1px solid var(--border); }
  tbody tr:nth-child(even) td { background: rgba(255,255,255,.025); }
  tr:hover td { background: var(--bg2); }
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
  .mini-stats b.money-pos { color: var(--green); }
  .mini-stats b.money-neg { color: var(--red); }
  .status-tag { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px; }
  .status-tag.dentro { background: rgba(16,185,129,.15); color: var(--green); }
  .status-tag.alem { background: rgba(239,68,68,.15); color: var(--red); }
  /* grade labeled heatmap (10 colunas) */
  .grade-wrap { display: grid; grid-template-columns: 32px repeat(10, 1fr); gap: 4px; align-items: center; }
  .grade-wrap .rowhead, .grade-wrap .colhead { text-align: center; font-size: 10px; color: var(--muted); font-weight: 700; }
  .grade-wrap .heatcell { padding: 8px 2px; font-size: 12px; }
  /* blocos — ranking de frequência individual por número (5 blocos de 10) */
  .blocos-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
  @media (max-width: 1100px) { .blocos-grid { grid-template-columns: repeat(3, 1fr); } }
  @media (max-width: 900px) { .blocos-grid { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 560px) { .blocos-grid { grid-template-columns: 1fr; } }
  .bloco-card { background: #10131c; border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
  .bloco-card h3 { font-size: 12px; font-weight: 700; color: var(--accent2); margin-bottom: 12px; text-transform: uppercase; letter-spacing: .5px; }
  .bloco-rank-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 11px; }
  .bloco-rank-row .medalha { width: 14px; flex-shrink: 0; text-align: center; font-size: 12px; }
  .bloco-num { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border-radius: 6px; background: #0f1117; border: 1px solid var(--border); font-weight: 700; font-size: 11px; flex-shrink: 0; }
  .bloco-num.top { background: linear-gradient(135deg,#f59e0b,#fbbf24); color: #1a1300; border-color: #f59e0b; }
  .bloco-num.bottom { background: rgba(239,68,68,.15); color: #fca5a5; border-color: rgba(239,68,68,.5); }
  .bloco-bar-wrap { flex: 1; background: #0f1117; border-radius: 4px; height: 12px; overflow: hidden; }
  .bloco-bar { height: 100%; background: var(--accent); border-radius: 4px; }
  .bloco-rank-row.top .bloco-bar { background: linear-gradient(90deg,#f59e0b,#fbbf24); }
  .bloco-rank-row.bottom .bloco-bar { background: rgba(239,68,68,.6); }
  .bloco-rank-row .contagem { width: 82px; text-align: right; color: var(--muted); flex-shrink: 0; }
  .window-btns { display: flex; gap: 6px; margin-bottom: 14px; }
  .hotcold-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(88px, 1fr)); gap: 8px; margin-top: 4px; }
  .hotcold-card { border-radius: 8px; padding: 10px 6px; text-align: center; border: 1px solid var(--border); background: #1e2130; }
  .hotcold-card.quente { background: rgba(239,68,68,.15); border-color: var(--red); }
  .hotcold-card.frio { background: rgba(251,113,133,.15); border-color: var(--accent2); }
  .hotcold-card .num { font-size: 16px; font-weight: 700; }
  .hotcold-card .status { font-size: 14px; }
  .hotcold-card .pct { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .hotcold-legend { display: flex; gap: 18px; margin-bottom: 12px; font-size: 12px; color: var(--muted); }
  .money-pos { color: var(--green); font-weight: 700; }
  .money-neg { color: var(--red); font-weight: 700; }
  /* comparativo 1ª x 2ª rodada — a identidade da Dupla Sena */
  .comp-rodadas-stats { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 16px; font-size: 12px; color: var(--muted); }
  .comp-rodadas-stats b { color: var(--text); font-size: 16px; display: block; font-family: var(--font-mono); }
  .comp-rodadas-stats .r1 { color: var(--accent2); }
  .comp-rodadas-stats .r2 { color: #93c5fd; }
  /* card "Sorteios repetidos" / "Rodadas idênticas" */
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
  .period-card-title { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-3); }
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
  .period-nivel2-opcoes { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
  .period-radio { display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 12px; color: var(--text); }
  .period-radio input { accent-color: var(--accent); cursor: pointer; }
  .period-nivel { animation: periodNivelIn .2s ease; }
  @keyframes periodNivelIn { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
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
  .periodo-banner { margin: 12px 24px 0; padding: 8px 14px; border-radius: var(--r-sm); font-size: 12px; font-weight: 600; }
  #periodo-banner { background: rgba(225,29,72,0.08); border: 1px solid rgba(225,29,72,0.2); color: var(--accent2); }
  #update-banner { background: rgba(245,158,11,.12); border: 1px solid var(--accent4); color: #fbbf24; }
  .update-banner { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .update-banner button { background: #f59e0b; border: none; border-radius: 8px; min-height: 36px; padding: 0 16px; color: #1a1300; font-weight: 700; cursor: pointer; font-size: 12px; flex-shrink: 0; transition: background .15s, transform .1s; }
  .update-banner button:hover { background: #fbbf24; }
  .update-banner button:active { transform: scale(.97); }
  .fin-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; }
  .fin-item { background: #10131c; border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
  .fin-item .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 6px; }
  .fin-item .value { font-size: 18px; font-weight: 700; color: var(--accent2); }
  .fin-item .sub { font-size: 11px; color: var(--muted); margin-top: 4px; }
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
  .hist-sorteio-row.hist-match { background: rgba(225,29,72,.12); }
  .hist-sorteio-row.hist-filtrado-fora { display: none; }
  .hist-mes.hist-fora-periodo, .hist-ano.hist-fora-periodo { display: none; }
  .hist-sorteio-row.hist-highlight { animation: histFlash 1.6s ease; }
  @keyframes histFlash { 0%, 100% { background: transparent; } 30% { background: rgba(190,18,60,.35); } }
  .hist-sorteio-detail { max-height: 0; overflow: hidden; transition: max-height .25s ease; margin-left: 24px; }
  .hist-detail-inner { padding: 10px 12px 14px; }
  .hist-detail-titulo { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
  .hist-rodada-label { font-size: 11px; font-weight: 700; color: var(--text-3); text-transform: uppercase; letter-spacing: .06em; margin: 10px 0 6px; }
  .hist-rodada-label:first-of-type { margin-top: 0; }
  .hist-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
  .hist-badge { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; background: var(--bg3); border: 1px solid var(--border-2); font-family: var(--font-mono); font-size: 12px; font-weight: 600; color: var(--text); margin: 2px; }
  .hist-badge.r1 { border-color: #7c6af7; color: #9d8fff; }
  .hist-badge.r2 { border-color: var(--blue); color: #93c5fd; }
  .hist-detail-meta { display: flex; gap: 20px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-top: 10px; }
  .hist-detail-meta b { color: var(--text); }
</style>
</head>
<body>
<header>
  <h1>🎲 Dupla Sena BI</h1>
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
    <div class="gh-modal-aviso">⚠️ Salvo apenas no seu navegador (localStorage). Nunca enviado para nenhum servidor nosso — só direto para a API do GitHub. Esse token é compartilhado com os outros dashboards deste repositório (Lotofácil, Mega-Sena etc.).</div>
    <div class="gh-modal-erro" id="gh-modal-erro" style="display:none;"></div>
    <div class="gh-modal-acoes">
      <button type="button" id="gh-modal-remover" style="display:none;">Remover token</button>
      <button type="button" id="gh-modal-cancelar">Cancelar</button>
      <button type="button" id="gh-modal-salvar">Salvar e atualizar</button>
    </div>
  </div>
</div>

<div class="page-tabs">
  <button class="page-tab active" id="page-tab-geral">Visão Geral</button>
  <button class="page-tab" id="page-tab-blocos">Blocos</button>
  <button class="page-tab" id="page-tab-historico">Histórico</button>
  <button class="page-tab" id="page-tab-simulador">Simulador de Jogo</button>
</div>

<div id="page-geral" class="page-content">
<!-- Grade interativa 5×10 (50 números) — clique filtra os gráficos abaixo -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card numgrid-card">
    <h2>🎯 Grade interativa — clique nas dezenas para filtrar os gráficos</h2>
    <div class="numgrid" id="numgrid"></div>
    <div class="numgrid-footer">
      <span class="numgrid-hint" id="numgrid-hint">Clique em uma ou mais dezenas para filtrar todos os gráficos abaixo pela combinação escolhida. O filtro considera os sorteios das 2 rodadas combinadas.</span>
      <button type="button" class="btn-secondary" id="numgrid-limpar" style="display:none;">Limpar seleção</button>
    </div>
  </div>
</div>
<div class="kpis">
  <div class="kpi"><span class="kpi-icon">📊</span><div class="label">Sorteios analisados (2 rodadas)</div><div class="value" id="kpi-total">—</div></div>
  <div class="kpi"><span class="kpi-icon">🎫</span><div class="label">Concursos</div><div class="value" id="kpi-concursos">—</div></div>
  <div class="kpi"><span class="kpi-icon">🔥</span><div class="label">Número mais frequente</div><div class="value" id="kpi-top1">—</div><div class="sub" id="kpi-top1-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">❄️</span><div class="label">Número menos frequente</div><div class="value" id="kpi-bot1">—</div><div class="sub" id="kpi-bot1-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">⚖️</span><div class="label">Média pares/sorteio</div><div class="value" id="kpi-pares">—</div></div>
  <div class="kpi"><span class="kpi-icon">➕</span><div class="label">Soma média das 6 dez.</div><div class="value" id="kpi-soma">—</div></div>
  <div class="kpi"><span class="kpi-icon">🔗</span><div class="label">Maior sequência vista</div><div class="value" id="kpi-seq">—</div><div class="sub">números consecutivos</div></div>
  <div class="kpi"><span class="kpi-icon">🎲</span><div class="label">Interseção média entre rodadas</div><div class="value" id="kpi-intersecao">—</div><div class="sub" id="kpi-intersecao-sub"></div></div>
</div>

<!-- Resumo financeiro do período selecionado -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>💰 Resumo financeiro (faixa sena — 6 acertos, das 2 rodadas)</h2>
    <div class="fin-grid" id="financeiro-resumo"></div>
  </div>
</div>

<!-- Frequência + Pares/Ímpares + Faixas -->
<div class="grid grid-3">
  <div class="card">
    <h2>📊 Frequência por dezena (2 rodadas combinadas)</h2>
    <canvas id="chartFreq"></canvas>
  </div>
  <div class="card">
    <h2>⚖️ Pares vs. Ímpares</h2>
    <canvas id="chartPI"></canvas>
    <div style="margin-top:14px; display:flex; gap:24px; justify-content:center; font-size:13px;">
      <span><span style="color:#e11d48">■</span> Pares: <b id="pct-pares">—</b></span>
      <span><span style="color:#fb7185">■</span> Ímpares: <b id="pct-impares">—</b></span>
    </div>
  </div>
  <div class="card">
    <h2>📈 Distribuição por faixa (média por sorteio)</h2>
    <canvas id="chartFaixas"></canvas>
  </div>
</div>

<!-- Soma + distribuição de interseção entre rodadas -->
<div class="grid grid-2">
  <div class="card">
    <h2>➕ Distribuição da soma das 6 dezenas</h2>
    <canvas id="chartSoma"></canvas>
  </div>
  <div class="card">
    <h2>🎲 Dezenas repetidas entre a 1ª e a 2ª rodada do mesmo concurso</h2>
    <canvas id="chartDistIntersecoes"></canvas>
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

<!-- Co-ocorrência + Anti-correlação -->
<div class="grid grid-2">
  <div class="card">
    <h2>🤝 Top 20 pares que mais saíram juntos</h2>
    <div id="cooc-list" style="overflow-y:auto; max-height:300px;"></div>
  </div>
  <div class="card">
    <h2>🙅 Top 15 pares que menos saíram juntos</h2>
    <div id="anticorr-list" style="overflow-y:auto; max-height:300px;"></div>
  </div>
</div>

<!-- 1ª rodada vs. 2ª rodada — a identidade da Dupla Sena -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="comp-rodadas-card">
    <h2>🎲 1ª Rodada vs. 2ª Rodada</h2>
    <div class="comp-rodadas-stats">
      <div>Dezena mais sorteada — 1ª rodada<b class="r1" id="comp-top-r1">—</b></div>
      <div>Dezena mais sorteada — 2ª rodada<b class="r2" id="comp-top-r2">—</b></div>
      <div>Interseção média por concurso<b id="comp-intersecao">—</b></div>
      <div>Esperado (sorteios independentes)<b id="comp-intersecao-teorica">—</b></div>
    </div>
    <canvas id="chartComparativo"></canvas>
    <div class="grid grid-2" style="padding:20px 0 0;">
      <div>
        <h2 style="margin-bottom:10px;">Mais favorecidas na 1ª rodada</h2>
        <div id="comp-skew-r1" style="overflow-x:auto;"></div>
      </div>
      <div>
        <h2 style="margin-bottom:10px;">Mais favorecidas na 2ª rodada</h2>
        <div id="comp-skew-r2" style="overflow-x:auto;"></div>
      </div>
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
    <h2>🔄 Sorteios repetidos (entre concursos, pool das 2 rodadas)</h2>
    <div id="repeticoes-conteudo"></div>
  </div>
</div>

<!-- Rodadas idênticas — curiosidade exclusiva da Dupla Sena -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="rodadas-identicas-card">
    <h2>🎯 Rodadas idênticas (1ª = 2ª no mesmo concurso)</h2>
    <div id="rodadas-identicas-conteudo"></div>
  </div>
</div>


</div><!-- /page-geral -->

<div id="page-blocos" class="page-content" style="display:none;">
<div class="kpis">
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco A (01-10) — campeão</div><div class="value" id="kpi-bloco-a">—</div><div class="sub" id="kpi-bloco-a-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco B (11-20) — campeão</div><div class="value" id="kpi-bloco-b">—</div><div class="sub" id="kpi-bloco-b-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco C (21-30) — campeão</div><div class="value" id="kpi-bloco-c">—</div><div class="sub" id="kpi-bloco-c-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco D (31-40) — campeão</div><div class="value" id="kpi-bloco-d">—</div><div class="sub" id="kpi-bloco-d-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco E (41-50) — campeão</div><div class="value" id="kpi-bloco-e">—</div><div class="sub" id="kpi-bloco-e-sub"></div></div>
</div>

<!-- 1. Ranking de frequência individual por número, dentro de cada bloco -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🏅 Ranking de frequência por bloco (01-10, 11-20, ..., 41-50)</h2>
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
    <h2>🔗 Co-ocorrência entre blocos (≥2 dezenas no mesmo sorteio)</h2>
    <div class="grade-wrap" id="blocos-coocorrencia" style="grid-template-columns: 40px repeat(5, 1fr);"></div>
  </div>
</div>

<!-- 4. Consistência entre rodadas — média de dezenas por bloco em cada rodada -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🎲 Consistência entre rodadas — média de dezenas por bloco</h2>
    <p style="color:var(--muted); font-size:12px; margin-bottom:14px;">Como as 2 rodadas são sorteios independentes do mesmo universo, as médias por bloco devem ficar parecidas entre si.</p>
    <div id="blocos-consistencia-rodadas" style="overflow-x:auto;"></div>
  </div>
</div>

<!-- 5. Blocos por período -->
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
    <div><span class="label">Último concurso</span><b id="hist-ultimo">—</b></div>
    <div><span class="label">Total</span><b id="hist-total">—</b></div>
  </div>
</div>

<div class="card hist-controles">
  <div class="hist-controle-grupo">
    <label for="hist-busca-concurso">Buscar concurso</label>
    <div class="sim-box" style="margin-bottom:0; display:flex; gap:10px;">
      <input type="text" id="hist-busca-concurso" placeholder="ex: 2800" style="flex:1; min-width:0; background:#0f1117; border:1px solid var(--border); border-radius:6px; padding:9px 12px; color:var(--text); font-size:13px;"/>
      <button id="hist-btn-buscar" class="btn-secondary">Ir</button>
    </div>
    <div class="sim-error" id="hist-busca-erro" style="display:none; color:var(--red); font-size:12px; margin-top:6px;"></div>
  </div>
  <div class="hist-controle-grupo">
    <label for="hist-filtro-dezena">Filtrar por dezena (01-50, em qualquer rodada)</label>
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

<div id="page-simulador" class="page-content" style="display:none;">
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🎱 Simulador de Jogo</h2>
    <p style="color:var(--muted); font-size:12px; margin-bottom:14px;">
      Clique nas dezenas para montar seu jogo (6 números de 01 a 50) e veja como ele teria se saído contra as duas rodadas do período selecionado.
    </p>
    <div class="simball-grid" id="simball-grid"></div>
    <div class="simball-footer">
      <span class="simball-contador" id="simball-contador">0/6 selecionados</span>
      <div style="display:flex; gap:8px;">
        <button class="btn-secondary" id="simball-limpar" type="button">Limpar</button>
        <button class="btn-primary" id="simball-btn" type="button" disabled>Simular ▶</button>
      </div>
    </div>
    <div class="sim-error" id="simball-error" style="display:none;"></div>
    <div id="simball-result"></div>
  </div>
</div>
</div><!-- /page-simulador -->

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

// ── módulos que reagem ao seletor de período ─────────────────────────────────

function renderKpisPeriodo(bundle) {
  const freq = bundle.frequencia;
  const sorted_freq = Object.entries(freq).map(([d,c])=>({d:+d,c})).sort((a,b)=>b.c-a.c);
  animarContador(document.getElementById('kpi-total'), bundle.meta.total);
  animarContador(document.getElementById('kpi-concursos'), bundle.meta.total_concursos != null ? bundle.meta.total_concursos : bundle.meta.total / 2);
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
  const pctPares = (avgPares/6*100).toFixed(1);
  document.getElementById('pct-pares').textContent = pctPares + '%';
  document.getElementById('pct-impares').textContent = (100-pctPares).toFixed(1) + '%';
  const comp = bundle.comparativo_rodadas;
  if (comp) {
    animarContador(document.getElementById('kpi-intersecao'), comp.media_intersecao, 2);
    document.getElementById('kpi-intersecao-sub').textContent = 'esperado (independentes): ' + comp.expectativa_teorica;
  }
}

// grade interativa 5×10 — clique filtra os gráficos abaixo
let numerosSelecionados = new Set();
let hotcoldJanelaAtual = 30;
const UNIVERSO_MIN = 1, UNIVERSO_MAX = 50;

// ── paleta de calor percentil (8 tons, cinza → crimson → dourado) — usada pela
// grade interativa de números e por qualquer outro heatmap por frequência do
// dashboard ──────────────────────────────────────────────────────────────────
const HEAT_STOPS = [
  { bg: '#1a1a24', fg: '#404058' },
  { bg: '#2a1420', fg: '#e11d48' },
  { bg: '#3a1628', fg: '#fb7185' },
  { bg: '#4a1a30', fg: '#fda4af' },
  { bg: '#5a2038', fg: '#fecdd3' },
  { bg: '#6e2c44', fg: '#ffe4e6' },
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

function renderNumGrid(bundle) {
  const grid = document.getElementById('numgrid');
  grid.innerHTML = '';
  const freq = bundle.frequencia;
  const entradas = Object.entries(freq).map(([d, c]) => ({ d: +d, c }));
  const ordenado = [...entradas].sort((a, b) => a.c - b.c);
  const rank = new Map(ordenado.map((e, i) => [e.d, i / Math.max(1, ordenado.length - 1)]));
  for (let d = UNIVERSO_MIN; d <= UNIVERSO_MAX; d++) {
    const cnt = freq[d] || 0;
    const p = rank.get(d) ?? 0;
    const { bg, fg } = stopFor(p);
    const cell = document.createElement('div');
    cell.className = 'numgrid-cell' + (numerosSelecionados.has(d) ? ' selecionada' : '');
    cell.dataset.num = String(d);
    cell.style.background = bg;
    cell.style.color = fg;
    cell.textContent = String(d).padStart(2, '0');
    const pct = bundle.meta.total ? (cnt/bundle.meta.total*100).toFixed(1) : '0.0';
    cell.title = `Dezena ${String(d).padStart(2,'0')}: ${cnt} vezes (${pct}%)`;
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
      datasets: [{ data: [total_p, total_i], backgroundColor: ['#e11d48','#fb7185'], borderWidth: 0 }]
    },
    options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { labels: { color: '#94a3b8' } } }, cutout: '65%' }
  });
}

function renderChartFreq(bundle) {
  const sorted_freq = Object.entries(bundle.frequencia).map(([d,c])=>({d:+d,c})).sort((a,b)=>b.c-a.c);
  const labels = sorted_freq.map(x => String(x.d).padStart(2,'0'));
  const values = sorted_freq.map(x => x.c);
  const colors = values.map((_,i) => i < 10 ? '#e11d48' : i > 39 ? '#ef4444' : '#334155');
  criarChart('chartFreq', {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Vezes sorteada', data: values, backgroundColor: colors, borderRadius: 4 }] },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderChartFaixas(bundle) {
  const n = bundle.faixas.length;
  const media = { baixo: 0, medio: 0, alto: 0 };
  bundle.faixas.forEach(f => { media.baixo += f.baixo; media.medio += f.medio; media.alto += f.alto; });
  criarChart('chartFaixas', {
    type: 'bar',
    data: {
      labels: ['Baixo (01-17)', 'Médio (18-34)', 'Alto (35-50)'],
      datasets: [{ label: 'Média de dezenas por sorteio', data: [media.baixo/n, media.medio/n, media.alto/n].map(v=>+v.toFixed(2)), backgroundColor: ['#fb7185','#e11d48','#9f1239'], borderRadius: 4 }]
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
      datasets: [{ label: 'Sorteios', data: labels.map(b => buckets[b]), backgroundColor: '#e11d48', borderRadius: 4 }]
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

// ── 1ª rodada vs. 2ª rodada — comparativo pré-computado em Python
// (calc_comparativo_rodadas), recalculado em JS pra mesclagem multi-ano
// (calcComparativoRodadasJS) mas NUNCA para o filtro de números da grade
// interativa (mesma exceção documentada pra blocos/financeiro: o comparativo
// continua mostrando o período ativo, não o subconjunto filtrado). ──────────
function renderComparativoRodadas(bundle) {
  const comp = bundle.comparativo_rodadas;
  if (!comp) return;
  document.getElementById('comp-top-r1').textContent = comp.top_r1.d != null ? String(comp.top_r1.d).padStart(2,'0') + ' (' + comp.top_r1.c + 'x)' : '—';
  document.getElementById('comp-top-r2').textContent = comp.top_r2.d != null ? String(comp.top_r2.d).padStart(2,'0') + ' (' + comp.top_r2.c + 'x)' : '—';
  document.getElementById('comp-intersecao').textContent = comp.media_intersecao;
  document.getElementById('comp-intersecao-teorica').textContent = comp.expectativa_teorica;

  const linhas = [];
  for (let d = UNIVERSO_MIN; d <= UNIVERSO_MAX; d++) {
    const r1 = comp.freq_r1[d] || 0, r2 = comp.freq_r2[d] || 0;
    linhas.push({ d, delta: r1 - r2 });
  }
  linhas.sort((a, b) => b.delta - a.delta);
  criarChart('chartComparativo', {
    type: 'bar',
    data: {
      labels: linhas.map(x => String(x.d).padStart(2,'0')),
      datasets: [{ label: 'Δ 1ª rodada − 2ª rodada (vezes)', data: linhas.map(x => x.delta), backgroundColor: linhas.map(x => x.delta >= 0 ? '#e11d48' : '#3b82f6'), borderRadius: 4 }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });

  function tabelaSkew(id, itens) {
    const el = document.getElementById(id);
    el.innerHTML = '';
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th>Dezena</th><th>1ª rodada</th><th>2ª rodada</th><th>Δ</th></tr></thead>';
    const tbody = document.createElement('tbody');
    itens.forEach(item => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span class="seq-tag">${String(item.d).padStart(2,'0')}</span></td>
        <td>${item.r1}</td>
        <td>${item.r2}</td>
        <td>${item.delta >= 0 ? '+' : ''}${item.delta}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    el.appendChild(table);
  }
  tabelaSkew('comp-skew-r1', comp.skew_r1);
  tabelaSkew('comp-skew-r2', comp.skew_r2);

  const distEntradas = Object.entries(comp.dist_intersecoes).map(([k,v]) => [+k, v]).sort((a,b) => a[0]-b[0]);
  criarChart('chartDistIntersecoes', {
    type: 'bar',
    data: {
      labels: distEntradas.map(([k]) => String(k)),
      datasets: [{ label: 'Concursos', data: distEntradas.map(([,v]) => v), backgroundColor: '#e11d48', borderRadius: 4 }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

// ── Números quentes e frios — janela de recência (15/30/50) sobre
// bundle.sorteios_raw (pool das 2 rodadas, já interleaved) ─────────────────
function renderHotCold(bundle, janela) {
  hotcoldJanelaAtual = janela;
  const grid = document.getElementById('hotcold-grid');
  grid.innerHTML = '';
  const sorteiosPeriodo = bundle.sorteios_raw || [];
  const totalPeriodo = sorteiosPeriodo.length;
  const janelaEfetiva = Math.min(janela, totalPeriodo);
  const recentes = sorteiosPeriodo.slice(-janelaEfetiva);
  const freqRec = {};
  for (let d = UNIVERSO_MIN; d <= UNIVERSO_MAX; d++) freqRec[d] = 0;
  recentes.forEach(s => s.forEach(d => freqRec[d]++));

  for (let d = UNIVERSO_MIN; d <= UNIVERSO_MAX; d++) {
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
  const faixaLabel = { A: '01 a 10', B: '11 a 20', C: '21 a 30', D: '31 a 40', E: '41 a 50' };
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
    const ordenado = [...vals].sort((a, b2) => a - b2);
    const percentilDoValor = (v) => ordenado.length > 1 ? ordenado.indexOf(v) / (ordenado.length - 1) : 0;
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
        const p = percentilDoValor(cnt);
        const { bg, fg } = stopFor(p);
        const cell = document.createElement('div');
        cell.className = 'heatcell';
        cell.style.background = bg;
        cell.style.color = fg;
        cell.style.fontFamily = 'var(--font-mono)';
        cell.style.fontSize = '12px';
        cell.textContent = cnt;
        cell.title = i === j
          ? `Bloco ${nomeLinha} sozinho com ≥2 dezenas: ${cnt} vezes`
          : `Blocos ${nomeLinha} e ${nomeCol} juntos com ≥2 dezenas cada: ${cnt} vezes`;
        wrap.appendChild(cell);
      });
    });
  }

  {
    const el = document.getElementById('blocos-consistencia-rodadas');
    el.innerHTML = '';
    const nota = bundle.blocos_rodada_nota;
    if (!nota) { el.innerHTML = '<p style="color:var(--muted)">Sem dados suficientes neste período.</p>'; }
    else {
      const table = document.createElement('table');
      table.innerHTML = `<thead><tr><th>Bloco</th><th>Média 1ª rodada</th><th>Média 2ª rodada</th><th>Δ</th></tr></thead>`;
      const tbody = document.createElement('tbody');
      nomes.forEach((nome, i) => {
        const m1 = nota.r1[i], m2 = nota.r2[i];
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${nome} (${faixaLabel[nome]})</td><td>${m1}</td><td>${m2}</td><td>${(m1-m2>=0?'+':'')}${(m1-m2).toFixed(2)}</td>`;
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      el.appendChild(table);
    }
  }
}

function renderFinanceiro(bundle) {
  const el = document.getElementById('financeiro-resumo');
  el.innerHTML = '';
  const f = bundle.financeiro;
  if (!f || !f.total_concursos) {
    el.innerHTML = '<p style="color:var(--muted)">Sem dados financeiros neste período.</p>';
    return;
  }
  const itens = [
    { label: 'Concursos analisados', valor: f.total_concursos },
    { label: 'Sorteios (2 rodadas/concurso)', valor: f.total_sorteios },
    { label: 'Prêmio médio — 1ª rodada', valor: f.sena1.media != null ? formatarMoeda(f.sena1.media) : '—' },
    {
      label: 'Maior prêmio — 1ª rodada',
      valor: f.sena1.maior ? formatarMoeda(f.sena1.maior.valor) : '—',
      sub: f.sena1.maior ? `Concurso ${f.sena1.maior.concurso} — ${f.sena1.maior.data}` : '',
    },
    { label: 'Prêmio médio — 2ª rodada', valor: f.sena2.media != null ? formatarMoeda(f.sena2.media) : '—' },
    {
      label: 'Maior prêmio — 2ª rodada',
      valor: f.sena2.maior ? formatarMoeda(f.sena2.maior.valor) : '—',
      sub: f.sena2.maior ? `Concurso ${f.sena2.maior.concurso} — ${f.sena2.maior.data}` : '',
    },
    { label: 'Concursos acumulados', valor: f.total_acumulados, sub: `${f.pct_acumulados}% do período` },
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
  banner.textContent = `⚡ Exibindo: ${label} · ${bundle.meta.total} sorteios (${bundle.meta.total_concursos != null ? bundle.meta.total_concursos : Math.round(bundle.meta.total/2)} concursos) · ${inicio}–${fim}`;
  banner.style.display = 'block';
}

function renderPeriodoCompleto(bundle) {
  renderKpisPeriodo(bundle);
  renderPI(bundle);
  renderChartFreq(bundle);
  renderChartFaixas(bundle);
  renderChartSoma(bundle);
  renderSeqTabs(bundle);
  renderCoocList(bundle);
  renderAntiCorr(bundle);
  renderComparativoRodadas(bundle);
  renderHotCold(bundle, hotcoldJanelaAtual);
  renderBlocos(bundle);
}

// ── grade interativa 5×10 — filtro client-side por combinação de dezenas ────
// (recalcula em JS os módulos period-aware sobre o subconjunto de sorteios
// que contêm TODAS as dezenas selecionadas, porque não dá pra pré-computar
// todas as combinações possíveis no servidor. blocos, financeiro e o
// comparativo de rodadas continuam mostrando o período ativo, não o
// subconjunto filtrado — mesma exceção documentada em megasena_bi.py.)

function calcFrequenciaJS(sorteios) {
  const freq = {};
  for (let d = UNIVERSO_MIN; d <= UNIVERSO_MAX; d++) freq[d] = 0;
  sorteios.forEach(s => s.forEach(d => freq[d]++));
  return freq;
}
function calcParesImparesJS(sorteios) {
  return sorteios.map(s => { const p = s.filter(d => d % 2 === 0).length; return { pares: p, impares: 6 - p }; });
}
function calcFaixasJS(sorteios) {
  return sorteios.map(s => ({
    baixo: s.filter(d => d >= 1 && d <= 17).length,
    medio: s.filter(d => d >= 18 && d <= 34).length,
    alto: s.filter(d => d >= 35 && d <= 50).length,
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

// ── mirrors JS de calc_blocos_bundle / calc_financeiro / calc_comparativo_rodadas
// — usados na mesclagem client-side de múltiplos anos (Melhoria 2), porque
// pré-computar todas as combinações possíveis de anos no servidor seria
// inviável (2^N combinações). ────────────────────────────────────────────────

function calcBlocosJS(sorteios) {
  const blocoDe = d => Math.floor((d - 1) / 10);
  const nomes = ['A', 'B', 'C', 'D', 'E'];
  const freqIndividual = {};
  nomes.forEach((nome, i) => {
    const inicio = i * 10 + 1;
    freqIndividual[nome] = {};
    for (let d = inicio; d < inicio + 10; d++) freqIndividual[nome][d] = 0;
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
    const ativos = [0, 1, 2, 3, 4].filter(i => c[i] >= 2);
    ativos.forEach(i => {
      matriz[i][i]++;
      ativos.forEach(j => { if (i !== j) matriz[i][j]++; });
    });
  });

  return { freq_individual: freqIndividual, combinacoes, coocorrencia: matriz };
}

// separa sorteiosRaw/sorteiosMeta (nível "1 entrada por rodada") em listas
// paralelas r1/r2 pela ordem de concurso — espelha o zip(r1_p, r2_p) do Python
function separarRodadasJS(sorteiosRaw, sorteiosMeta) {
  const r1 = [], r2 = [];
  for (let i = 0; i < sorteiosRaw.length; i++) {
    const m = sorteiosMeta[i];
    if (!m) continue;
    if (m.rodada === 1) r1.push(sorteiosRaw[i]);
    else if (m.rodada === 2) r2.push(sorteiosRaw[i]);
  }
  return [r1, r2];
}

function calcComparativoRodadasJS(r1List, r2List) {
  const freqR1 = calcFrequenciaJS(r1List);
  const freqR2 = calcFrequenciaJS(r2List);
  const linhas = [];
  for (let d = UNIVERSO_MIN; d <= UNIVERSO_MAX; d++) {
    const f1 = freqR1[d] || 0, f2 = freqR2[d] || 0;
    linhas.push({ d, r1: f1, r2: f2, delta: f1 - f2 });
  }
  const skewR1 = [...linhas].sort((a, b) => b.delta - a.delta).slice(0, 10);
  const skewR2 = [...linhas].sort((a, b) => a.delta - b.delta).slice(0, 10);
  let topR1 = { d: null, c: 0 }, topR2 = { d: null, c: 0 };
  Object.entries(freqR1).forEach(([d, c]) => { if (c > topR1.c) topR1 = { d: +d, c }; });
  Object.entries(freqR2).forEach(([d, c]) => { if (c > topR2.c) topR2 = { d: +d, c }; });
  const n = Math.min(r1List.length, r2List.length);
  const intersecoes = [];
  for (let i = 0; i < n; i++) {
    const setB = new Set(r2List[i]);
    intersecoes.push(r1List[i].filter(x => setB.has(x)).length);
  }
  const media = intersecoes.length ? intersecoes.reduce((a, b) => a + b, 0) / intersecoes.length : 0;
  const distIntersecoes = {};
  intersecoes.forEach(v => { distIntersecoes[v] = (distIntersecoes[v] || 0) + 1; });
  return {
    freq_r1: freqR1, freq_r2: freqR2,
    top_r1: topR1, top_r2: topR2,
    skew_r1: skewR1, skew_r2: skewR2,
    media_intersecao: +media.toFixed(3),
    expectativa_teorica: +(6 * 6 / 50).toFixed(2),
    dist_intersecoes: distIntersecoes,
  };
}

function calcBlocosRodadaNotaJS(r1List, r2List) {
  const blocoDe = d => Math.floor((d - 1) / 10);
  function medias(sorteios) {
    const n = sorteios.length;
    if (!n) return [0, 0, 0, 0, 0];
    const soma = [0, 0, 0, 0, 0];
    sorteios.forEach(s => { s.forEach(d => { soma[blocoDe(d)]++; }); });
    return soma.map(v => +(v / n).toFixed(2));
  }
  return { r1: medias(r1List), r2: medias(r2List) };
}

function calcFinanceiroJS(sorteiosMeta) {
  const porRodada = (r) => sorteiosMeta.filter(m => m.rodada === r);
  function stats(lista) {
    const vals = lista.filter(m => m.valor_sena != null);
    if (!vals.length) return { media: null, maior: null };
    const media = +(vals.reduce((a, m) => a + m.valor_sena, 0) / vals.length).toFixed(2);
    const maior = vals.reduce((mx, m) => (m.valor_sena > mx.valor_sena ? m : mx));
    return { media, maior: { valor: maior.valor_sena, concurso: maior.concurso, data: maior.data } };
  }
  const m1 = porRodada(1), m2 = porRodada(2);
  const n = m1.length;
  const totalAcumulados = m1.filter(m => m.acumulado).length;
  return {
    total_concursos: n,
    total_sorteios: n * 2,
    sena1: stats(m1),
    sena2: stats(m2),
    total_acumulados: totalAcumulados,
    pct_acumulados: n ? +(totalAcumulados / n * 100).toFixed(1) : 0,
  };
}

function montarBundleFiltradoPorNumeros(sorteios, bundleBase) {
  const { distTamanho, topPorTamanho } = calcSequenciasJS(sorteios);
  return {
    meta: { total: sorteios.length, total_concursos: bundleBase.meta.total_concursos },
    frequencia: calcFrequenciaJS(sorteios),
    pares_impares: calcParesImparesJS(sorteios),
    faixas: calcFaixasJS(sorteios),
    somas: calcSomaJS(sorteios),
    seq_dist_tamanho: distTamanho,
    seq_top_por_tamanho: topPorTamanho,
    coocorrencia: calcCoocorrenciaJS(sorteios, 20),
    anticorrelacao: calcAntiCorrelacaoJS(sorteios, 15),
    // exceção documentada: comparativo/blocos/financeiro continuam do período
    // ativo, não são recalculados pro subconjunto filtrado por combinação
    comparativo_rodadas: bundleBase.comparativo_rodadas,
    blocos: bundleBase.blocos,
    blocos_rodada_nota: bundleBase.blocos_rodada_nota,
    financeiro: bundleBase.financeiro,
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
    hint.textContent = 'Clique em uma ou mais dezenas para filtrar todos os gráficos abaixo pela combinação escolhida. O filtro considera os sorteios das 2 rodadas combinadas.';
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
  if (hint) hint.textContent = 'Clique em uma ou mais dezenas para filtrar todos os gráficos abaixo pela combinação escolhida. O filtro considera os sorteios das 2 rodadas combinadas.';
  renderNumGrid(bundle);
  renderPeriodoCompleto(bundle);
  renderFinanceiro(bundle);
  renderBannerPeriodo(periodoId, bundle);
  renderBlocosMensal(periodoId);
  aplicarFiltroPeriodoHistorico(periodoId);
}

// ── mesclagem client-side de múltiplos anos selecionados (Melhoria 2) ────────
function calcularBundleCompletoJS(sorteiosRaw, sorteiosMeta) {
  const n = sorteiosRaw.length;
  const { distTamanho, topPorTamanho } = calcSequenciasJS(sorteiosRaw);
  const [r1List, r2List] = separarRodadasJS(sorteiosRaw, sorteiosMeta);
  return {
    meta: {
      total: n,
      total_concursos: r1List.length,
      inicio: n ? sorteiosMeta[0].data : null,
      fim: n ? sorteiosMeta[n - 1].data : null,
    },
    frequencia: calcFrequenciaJS(sorteiosRaw),
    pares_impares: calcParesImparesJS(sorteiosRaw),
    faixas: calcFaixasJS(sorteiosRaw),
    somas: calcSomaJS(sorteiosRaw),
    seq_dist_tamanho: distTamanho,
    seq_top_por_tamanho: topPorTamanho,
    coocorrencia: calcCoocorrenciaJS(sorteiosRaw, 20),
    anticorrelacao: calcAntiCorrelacaoJS(sorteiosRaw, 15),
    comparativo_rodadas: calcComparativoRodadasJS(r1List, r2List),
    blocos: calcBlocosJS(sorteiosRaw),
    blocos_rodada_nota: calcBlocosRodadaNotaJS(r1List, r2List),
    financeiro: calcFinanceiroJS(sorteiosMeta),
    sorteios_raw: sorteiosRaw,
    sorteios_meta: sorteiosMeta,
  };
}

function renderBannerPeriodoMulti(anos, bundle) {
  const banner = document.getElementById('periodo-banner');
  const label = [...anos].sort().join(' + ');
  banner.textContent = `⚡ Exibindo: ${label} · ${bundle.meta.total} sorteios (${bundle.meta.total_concursos} concursos)`;
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
  // por (concurso, rodada) pra manter a ordem cronológica e recalcula tudo no
  // cliente com as mesmas funções calc*JS da grade interativa.
  const pares = [];
  anos.forEach(ano => {
    const p = DATA.periodos[ano];
    if (!p) return;
    p.sorteios_raw.forEach((s, i) => pares.push([s, p.sorteios_meta[i]]));
  });
  pares.sort((a, b) => (a[1].concurso - b[1].concurso) || (a[1].rodada - b[1].rodada));
  const sorteiosRaw = pares.map(x => x[0]);
  const sorteiosMeta = pares.map(x => x[1]);

  modoMultiAno = true;
  periodoAtualId = 'MULTI:' + [...anos].sort().join(',');
  bundleAtualMerged = calcularBundleCompletoJS(sorteiosRaw, sorteiosMeta);

  numerosSelecionados.clear();
  const btnLimpar = document.getElementById('numgrid-limpar');
  const hint = document.getElementById('numgrid-hint');
  if (btnLimpar) btnLimpar.style.display = 'none';
  if (hint) hint.textContent = 'Clique em uma ou mais dezenas para filtrar todos os gráficos abaixo pela combinação escolhida. O filtro considera os sorteios das 2 rodadas combinadas.';
  renderNumGrid(bundleAtualMerged);
  renderPeriodoCompleto(bundleAtualMerged);
  renderFinanceiro(bundleAtualMerged);
  renderBannerPeriodoMulti(anos, bundleAtualMerged);
  renderBlocosMensalMulti(anos);
  aplicarFiltroPeriodoHistoricoMulti(anos);
}

// ── seletor de período cascateado: Ano(s) — multi-select (sempre visível) →
// tipo de período (Ano completo/Semestre/Trimestre/Bimestre/Mês, só aparece
// com EXATAMENTE 1 ano selecionado) → intervalo específico. 0 anos = "Todos
// os dados"; 2+ anos = mescla os anos selecionados (sem subdivisão). ───────
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

// ── abas de página: Visão Geral / Blocos / Histórico ─────────────────────────
{
  const paginas = [
    { tab: 'page-tab-geral', pagina: 'page-geral' },
    { tab: 'page-tab-blocos', pagina: 'page-blocos' },
    { tab: 'page-tab-historico', pagina: 'page-historico' },
    { tab: 'page-tab-simulador', pagina: 'page-simulador' },
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

// ── Blocos por período (heatmap mensal) — histórico completo só em "Todos";
// condensado aos meses do período ativo quando há filtro de ano/semestre/
// trimestre/bimestre; oculto quando o filtro já é 1 mês só. ─────────────────
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
  const ordenado = [...todasMedias].sort((a, b) => a - b);
  const percentilDaMedia = (v) => ordenado.length > 1 ? ordenado.indexOf(v) / (ordenado.length - 1) : 0;
  const table = document.createElement('table');
  table.innerHTML = `<thead><tr><th>Mês</th>${nomes.map(n => `<th>${n}</th>`).join('')}</tr></thead>`;
  const tbody = document.createElement('tbody');
  dados.forEach(d => {
    const tr = document.createElement('tr');
    let celulas = `<td style="color:var(--muted)">${d.periodo}</td>`;
    d.medias.forEach(v => {
      const p = percentilDaMedia(v);
      const { bg, fg } = stopFor(p);
      celulas += `<td style="background:${bg}; color:${fg}; font-family:var(--font-mono); text-align:center;">${v}</td>`;
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

// ── Sorteios repetidos + Rodadas idênticas (curiosidades — sempre histórico
// completo, não dependem do período ativo) ──────────────────────────────────
{
  const el = document.getElementById('repeticoes-conteudo');
  const reps = DATA.repeticoes || [];
  const prob = DATA.prob_repeticao_pct;
  const totalComb = DATA.total_combinacoes;
  const probTxt = totalComb
    ? `A probabilidade de pelo menos 1 repetição depois de ${DATA.meta.total} sorteios (pool das 2 rodadas) é de aproximadamente ${prob}% (1 em ${totalComb.toLocaleString('pt-BR')} combinações possíveis).`
    : '';
  if (!reps.length) {
    el.innerHTML = `
      <div class="repet-ok">
        <span class="icone">✅</span>
        <div>
          <p>Nenhum sorteio idêntico encontrado em ${DATA.meta.total} sorteios analisados.</p>
          <p class="prob">${probTxt}</p>
        </div>
      </div>`;
  } else {
    let html = `<p style="color:var(--muted); font-size:12px; margin-bottom:14px;">${reps.length} ocorrência${reps.length === 1 ? '' : 's'} encontrada${reps.length === 1 ? '' : 's'}. ${probTxt}</p>`;
    reps.forEach((rep, i) => {
      const dezenasTxt = rep.numeros.map(n => String(n).padStart(2, '0')).join(' · ');
      const sorteiosHtml = rep.sorteios.map(s => `<div class="repet-sorteio">→ Concurso <b>${s.concurso}</b> (${s.data}) — ${s.rodada}</div>`).join('');
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

{
  const el = document.getElementById('rodadas-identicas-conteudo');
  const achados = DATA.rodadas_identicas || [];
  if (!achados.length) {
    el.innerHTML = `
      <div class="repet-ok">
        <span class="icone">✅</span>
        <div>
          <p>Nenhum concurso em que a 1ª e a 2ª rodada sortearam exatamente as mesmas 6 dezenas.</p>
          <p class="prob">Astronomicamente improvável — há ${DATA.total_combinacoes.toLocaleString('pt-BR')} combinações possíveis de 6 dezenas em 50.</p>
        </div>
      </div>`;
  } else {
    let html = `<p style="color:var(--muted); font-size:12px; margin-bottom:14px;">${achados.length} concurso(s) encontrado(s) em que as 2 rodadas bateram exatamente.</p>`;
    achados.forEach((a, i) => {
      const dezenasTxt = a.numeros.map(n => String(n).padStart(2, '0')).join(' · ');
      html += `
        <div class="repet-item">
          <div class="repet-item-titulo">Concurso ${a.concurso} — ${a.data}</div>
          <div class="repet-numeros">${dezenasTxt}</div>
        </div>`;
    });
    el.innerHTML = html;
  }
}

// ── Histórico — árvore Ano → Mês → Concurso (cada card mostra as 2 rodadas) ─

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
  const tagsR1 = s.r1.map(n => `<span class="hist-badge r1">${String(n).padStart(2,'0')}</span>`).join('');
  const tagsR2 = s.r2.map(n => `<span class="hist-badge r2">${String(n).padStart(2,'0')}</span>`).join('');
  div.innerHTML = `
    <div class="hist-detail-titulo">Concurso ${s.concurso} — ${s.dia_semana}, ${formatarDataExtenso(s.data_iso)}</div>
    <div class="hist-rodada-label">1ª Rodada</div>
    <div class="hist-badges">${tagsR1}</div>
    <div class="hist-rodada-label">2ª Rodada</div>
    <div class="hist-badges">${tagsR2}</div>
    <div class="hist-detail-meta">
      <div>Acumulado: <b>${s.acumulado ? 'Sim' : 'Não'}</b></div>
      <div>Ganhadores sena — 1ª rodada: <b>${s.sena1_ganhadores != null ? s.sena1_ganhadores : '—'}</b></div>
      <div>Prêmio sena — 1ª rodada: <b>${formatarMoedaHist(s.sena1_valor)}</b></div>
      <div>Ganhadores sena — 2ª rodada: <b>${s.sena2_ganhadores != null ? s.sena2_ganhadores : '—'}</b></div>
      <div>Prêmio sena — 2ª rodada: <b>${formatarMoedaHist(s.sena2_valor)}</b></div>
    </div>`;
  return div;
}

function construirLinhaSorteio(s) {
  const row = document.createElement('div');
  row.className = 'hist-sorteio-row';
  row.dataset.concurso = s.concurso;
  row.dataset.dezenas = s.todas_dezenas.join(',');
  const arrow = document.createElement('span');
  arrow.className = 'hist-arrow';
  arrow.textContent = '▶';
  row.appendChild(arrow);

  const texto = document.createElement('span');
  texto.innerHTML = `${s.data_br} &nbsp;<span class="hist-concurso">Concurso ${s.concurso}</span>`;
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
  contador.textContent = `${mesNode.label} ${anoKey} — ${mesNode.total} concursos`;
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
  contador.textContent = `${anoKey} — ${anoNode.total} concursos`;
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
  document.getElementById('hist-total').textContent = `${h.total} concursos (${h.total_sorteios} sorteios)`;

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
    if (Number.isInteger(num) && num >= UNIVERSO_MIN && num <= UNIVERSO_MAX) filtrarPorDezena(num);
  });
  document.getElementById('hist-btn-limpar-filtro').addEventListener('click', () => {
    document.getElementById('hist-filtro-dezena').value = '';
    limparFiltroDezena();
  });

  aplicarFiltroPeriodoHistorico(periodoAtualId);
}

// ── Verificação leve de sorteios novos + botão "Atualizar dados" ────────────
// Só aparece quando o HTML foi gerado com --source supabase. O token do
// GitHub é o MESMO localStorage key dos outros dashboards deste repositório —
// configura uma vez, funciona em todos.
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

// ── Simulador de Jogo (bolinhas) — universo 01-50, exatamente 6 números.
// Dupla Sena nunca teve simulador de texto pra reaproveitar (fora do escopo
// original), então o cálculo é novo aqui — mas segue o mesmo padrão dos
// outros 3 dashboards: sorteios_raw/sorteios_meta já vêm agrupados (pool das
// 2 rodadas, sorteios_meta[i].rodada diz de qual rodada cada entrada é), e o
// prêmio de cada faixa (3/4/5/6 acertos) já vem pronto por rodada em
// sorteios_meta (valor_sena/quina/quadra/terno — ver _meta_sorteio_rodada no
// Python). Custo é por CONCURSO (uma aposta cobre as 2 rodadas), não por
// sorteio individual — por isso divide sorteiosRaw.length por 2. ───────────
const CUSTO_DUPLASENA_JS = 2.50;
const CAMPO_FAIXA_DUPLASENA_JS = { 6: 'valor_sena', 5: 'valor_quina', 4: 'valor_quadra', 3: 'valor_terno' };

function duplasenaSimularJogo(numeros, sorteiosRaw, sorteiosMeta) {
  const aposta = new Set(numeros);
  const pontos = { 1: { 3: 0, 4: 0, 5: 0, 6: 0 }, 2: { 3: 0, 4: 0, 5: 0, 6: 0 } };
  const premiados = [];
  let ganho = 0;
  sorteiosRaw.forEach((s, i) => {
    const acertos = s.filter(d => aposta.has(d)).length;
    if (acertos < 3) return;
    const meta = sorteiosMeta[i];
    if (!meta) return;
    const rodada = meta.rodada;
    pontos[rodada][acertos]++;
    const campo = CAMPO_FAIXA_DUPLASENA_JS[acertos];
    const premio = meta[campo] || 0;
    ganho += premio;
    premiados.push({ concurso: meta.concurso, data: meta.data, rodada, acertos, premio: +premio.toFixed(2) });
  });
  premiados.sort((a, b) => b.concurso - a.concurso);
  const totalConcursos = sorteiosRaw.length / 2;
  const custo = +(CUSTO_DUPLASENA_JS * totalConcursos).toFixed(2);
  ganho = +ganho.toFixed(2);
  const saldo = +(ganho - custo).toFixed(2);
  const roi = custo ? +(saldo / custo * 100).toFixed(1) : 0;
  return { numeros, pontos, premiados, totalConcursos, custo, ganho, saldo, roi };
}

function duplasenaRenderResultado(r, elId) {
  const el = document.getElementById(elId);
  el.innerHTML = '';
  const dezenasTxt = r.numeros.map(n => String(n).padStart(2, '0')).join(' · ');

  const titulo = document.createElement('div');
  titulo.style.cssText = 'font-weight:700; margin-bottom:10px;';
  titulo.textContent = `Jogo: ${dezenasTxt}`;
  el.appendChild(titulo);

  [1, 2].forEach(rodada => {
    const label = document.createElement('div');
    label.className = 'dsim-rodada-titulo';
    label.textContent = `${rodada}ª Rodada`;
    el.appendChild(label);
    const table = document.createElement('table');
    table.innerHTML = `<thead><tr><th>Faixa</th><th>Vezes que ocorreu</th></tr></thead>`;
    const tbody = document.createElement('tbody');
    [6, 5, 4, 3].forEach(p => {
      const tr = document.createElement('tr');
      const nomes = { 6: 'Sena (6 acertos)', 5: 'Quina (5 acertos)', 4: 'Quadra (4 acertos)', 3: 'Terno (3 acertos)' };
      tr.innerHTML = `<td class="pontos">${nomes[p]}</td><td>${r.pontos[rodada][p]}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    el.appendChild(table);
  });

  const finDiv = document.createElement('div');
  finDiv.className = 'mini-stats';
  finDiv.innerHTML = `
    <div>Concursos no período<b>${r.totalConcursos}</b></div>
    <div>Custo total<b>${formatarMoeda(r.custo)}</b></div>
    <div>Ganho estimado<b>${formatarMoeda(r.ganho)}</b></div>
    <div>Saldo<b class="${r.saldo >= 0 ? 'money-pos' : 'money-neg'}">${formatarMoeda(r.saldo)}</b></div>
    <div>ROI<b class="${r.roi >= 0 ? 'money-pos' : 'money-neg'}">${formatarPct(r.roi)}</b></div>`;
  el.appendChild(finDiv);

  if (r.premiados.length) {
    // resumo por rodada — "Duplo" = concursos que pontuaram nas 2 rodadas
    const r1 = r.premiados.filter(p => p.rodada === 1);
    const r2 = r.premiados.filter(p => p.rodada === 2);
    const concursosR1 = new Set(r1.map(p => p.concurso));
    const duplo = r2.filter(p => concursosR1.has(p.concurso)).length;
    const resumoRodadas = document.createElement('div');
    resumoRodadas.style.cssText = 'font-size:12px; color:var(--text-2); margin-top:10px;';
    resumoRodadas.textContent = `1ª Rodada: ${r1.length} premiações · 2ª Rodada: ${r2.length} · Duplo: ${duplo}`;
    el.appendChild(resumoRodadas);

    const btnToggle = document.createElement('button');
    btnToggle.className = 'sim-detalhe-toggle';
    btnToggle.style.marginTop = '10px';
    btnToggle.textContent = `Ver em quais concursos pontuou (${r.premiados.length})`;
    const painel = document.createElement('div');
    painel.className = 'sim-detalhe-painel';

    // lista compacta: só #concurso e data (sem detalhes de rodada/faixa) — os
    // 10 mais recentes por padrão, "ver todos" revela o resto
    const lista = document.createElement('div');
    lista.className = 'sim-historico';
    function renderChips(qtd) {
      lista.innerHTML = '';
      r.premiados.slice(0, qtd).forEach(p => {
        const item = document.createElement('div');
        item.className = 'sim-hist-item';
        item.innerHTML = `<span class="concurso">#${p.concurso}</span> ${p.data}`;
        lista.appendChild(item);
      });
    }
    renderChips(10);
    painel.appendChild(lista);
    if (r.premiados.length > 10) {
      const btnMais = document.createElement('button');
      btnMais.className = 'sim-detalhe-toggle sim-hist-mais';
      btnMais.textContent = `▼ ver todos (${r.premiados.length})`;
      btnMais.addEventListener('click', () => {
        renderChips(r.premiados.length);
        btnMais.remove();
      });
      painel.appendChild(btnMais);
    }

    btnToggle.addEventListener('click', () => {
      painel.classList.toggle('aberto');
      btnToggle.textContent = painel.classList.contains('aberto')
        ? 'Esconder concursos'
        : `Ver em quais concursos pontuou (${r.premiados.length})`;
    });
    el.appendChild(btnToggle);
    el.appendChild(painel);
  }
}

{
  const SIMBALL_MIN = 6;
  const grid = document.getElementById('simball-grid');
  const contadorEl = document.getElementById('simball-contador');
  const btnSimular = document.getElementById('simball-btn');
  const btnLimpar = document.getElementById('simball-limpar');
  const errBallEl = document.getElementById('simball-error');
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

  for (let n = 1; n <= 50; n++) {
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
    document.getElementById('simball-result').innerHTML = '';
  });

  btnSimular.addEventListener('click', () => {
    errBallEl.style.display = 'none';
    document.getElementById('simball-result').innerHTML = '';
    if (selecionadas.size !== SIMBALL_MIN) {
      errBallEl.textContent = `Selecione exatamente ${SIMBALL_MIN} dezenas.`;
      errBallEl.style.display = 'block';
      return;
    }
    const numeros = [...selecionadas].sort((a, b) => a - b);
    const bundle = bundleAtivo() || DATA;
    const sorteiosRaw = bundle.sorteios_raw || DATA.sorteios_raw;
    const sorteiosMeta = bundle.sorteios_meta || DATA.sorteios_meta || [];
    const resultado = duplasenaSimularJogo(numeros, sorteiosRaw, sorteiosMeta);
    duplasenaRenderResultado(resultado, 'simball-result');
  });
}
</script>
</body>
</html>
"""


def gerar_html(rows: list[dict], output: str, fonte_supabase: dict | None = None, github_repo: str | None = None):
    n = len(rows)
    bundle_todos = calcular_bundle_periodo(rows)

    blocos_periodo = calc_blocos_periodo(rows)
    periodos, periodos_disponiveis = gerar_periodos(rows)
    historico = calc_historico(rows)
    repeticoes = detectar_repeticoes(rows)
    rodadas_identicas = detectar_rodadas_identicas(rows)
    prob_repeticao_pct = prob_repeticao(bundle_todos["meta"]["total"], TOTAL_COMBINACOES)

    data = dict(bundle_todos)
    data["meta"] = {
        **bundle_todos["meta"],
        "concurso_ini": rows[0]["concurso"],
        "concurso_fim": rows[-1]["concurso"],
        "supabase": fonte_supabase,
        "github_repo": github_repo,
        "tabela": "duplasena_sorteios",
        "workflow_file": "duplasena_atualizar.yml",
    }
    data["blocos_periodo"] = blocos_periodo
    data["periodos"] = periodos
    data["periodos_disponiveis"] = periodos_disponiveis
    data["historico"] = historico
    data["repeticoes"] = repeticoes
    data["rodadas_identicas"] = rodadas_identicas
    data["prob_repeticao_pct"] = prob_repeticao_pct
    data["total_combinacoes"] = TOTAL_COMBINACOES

    from datetime import datetime
    titulo = f"{n} concursos — {rows[0]['concurso']} a {rows[-1]['concurso']}"
    subtitulo = f"Concurso {rows[-1]['concurso']} · {rows[-1]['data']} · {n} concursos ({n * 2} sorteios) analisados"
    gerado_em = f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}"

    html = (HTML_TEMPLATE
            .replace("{titulo}", titulo)
            .replace("{subtitulo}", subtitulo)
            .replace("{gerado_em}", gerado_em)
            .replace("{data_json}", json.dumps(data, ensure_ascii=False)))

    with open(output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✓ Dashboard salvo em '{output}'  ({n} concursos, {n * 2} sorteios)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera BI HTML dos sorteios da Dupla Sena")
    parser.add_argument("--db", default="duplasena.db", help="Caminho do banco SQLite (padrão: duplasena.db)")
    parser.add_argument("--source", choices=["supabase", "local"], default="supabase",
                         help="Fonte de dados: 'supabase' (padrão) ou 'local' (SQLite em --db)")
    parser.add_argument("--supabase-url", default=None)
    parser.add_argument("--supabase-key", default=None, help="SUPABASE_ANON_KEY (leitura pública)")
    parser.add_argument("--periodo", default=None, help="Filtra por ano (ex: 2025) ou prefixo ISO (ex: 2025-06)")
    parser.add_argument("--output", default="duplasena.html", help="Arquivo HTML de saída")
    parser.add_argument("--github-repo", default="andrevisc-1209/lotofacil-bi",
                         help="dono/repositorio — usado só pelo botão 'Atualizar dados'")
    args = parser.parse_args()

    fonte_supabase = None

    if args.source == "supabase":
        import duplasena_db
        url = args.supabase_url
        key = args.supabase_key
        if not (url and key):
            env_url, env_key = duplasena_db.carregar_credenciais_supabase()
            url = url or env_url
            key = key or env_key
        if not (url and key):
            print("SUPABASE_URL e SUPABASE_ANON_KEY precisam estar configurados "
                  "(--supabase-url/--supabase-key, variável de ambiente ou .env).")
            exit(1)
        db = duplasena_db.Database.supabase(url, key)
        rows = carregar_de_database(db)
        db.fechar()
        print(f"Carregados {len(rows)} concursos do Supabase.")
        fonte_supabase = {"url": url, "anon_key": key}
    elif args.source == "local":
        if not Path(args.db).exists():
            print(f"Banco '{args.db}' não encontrado. Rode primeiro: python duplasena_atualizar.py --init-all")
            exit(1)
        import duplasena_db
        db = duplasena_db.Database.sqlite(args.db)
        rows = carregar_de_database(db)
        db.fechar()
        print(f"Carregados {len(rows)} concursos de '{args.db}'.")
    else:
        print("Especifique --source supabase ou --source local (com --db).")
        exit(1)

    if not rows:
        print("Nenhum concurso encontrado na fonte de dados. Rode duplasena_atualizar.py primeiro.")
        exit(1)

    if args.periodo:
        rows = filtrar_por_periodo(rows, args.periodo)
        print(f"Filtrado para o período '{args.periodo}': {len(rows)} concursos.")
        if not rows:
            print("Nenhum concurso encontrado para esse período.")
            exit(1)

    gerar_html(rows, args.output, fonte_supabase=fonte_supabase, github_repo=args.github_repo)

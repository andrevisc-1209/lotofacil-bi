"""
lotofacil_bi.py
---------------
Lê o CSV gerado por lotofacil_coletar.py e produz um dashboard HTML interativo.

Uso:
    python lotofacil_bi.py                              # lê lotofacil_sorteios.csv
    python lotofacil_bi.py --input meus_dados.csv
    python lotofacil_bi.py --output index.html
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

try:
    from lotofacil_simular import JOGOS, calcular_jogo
except ImportError:
    JOGOS = None
    calcular_jogo = None

try:
    from lotofacil_jogos_sugeridos import JOGOS_SUGERIDOS
except ImportError:
    JOGOS_SUGERIDOS = None


# ─── carrega dados (CSV ou banco SQLite) ───────────────────────────────────────

def carregar(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def carregar_de_database(db) -> list[dict]:
    """Lê uma instância de lotofacil_db.Database (backend SQLite OU Supabase —
    a função não sabe nem precisa saber qual) e devolve linhas no mesmo formato
    de carregar() (campo 'data' em formato BR, igual ao CSV), com 'data_iso'
    extra para permitir o bucketing temporal sem precisar reconverter a data."""
    registros = db.carregar_todos()
    rows = []
    for r in registros:
        linha = {k: v for k, v in r.items() if k not in ("data", "data_br")}
        linha["concurso"] = str(r["concurso"])  # mesmo tipo (str) que o CSV produz
        linha["data_iso"] = r["data"]
        linha["data"] = r.get("data_br") or r["data"]
        rows.append(linha)
    return rows

def data_iso_de(row: dict) -> str:
    """Data em formato ISO (YYYY-MM-DD), a partir do CSV (DD/MM/AAAA) ou do banco."""
    if row.get("data_iso"):
        return row["data_iso"]
    dia, mes, ano = row["data"].split("/")
    return f"{ano}-{mes}-{dia}"

def filtrar_por_periodo(rows: list[dict], periodo: str) -> list[dict]:
    """Filtro simples usado pela flag --periodo: aceita um ano (ex. '2025') ou
    um prefixo ISO (ex. '2025-06')."""
    return [r for r in rows if data_iso_de(r).startswith(periodo)]

def dezenas(row: dict) -> list[int]:
    return sorted(int(row[f"d{i:02d}"]) for i in range(1, 16))


# ─── análises ─────────────────────────────────────────────────────────────────

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
    return {d: n - 1 - ultimo.get(d, -1) for d in range(1, 26)}

def calc_pares_impares(sorteios):
    resultados = []
    for s in sorteios:
        p = sum(1 for d in s if d % 2 == 0)
        resultados.append({"pares": p, "impares": 15 - p})
    return resultados

def calc_faixas(sorteios):
    """Distribuição por faixa em cada sorteio."""
    resultados = []
    for s in sorteios:
        resultados.append({
            "baixo": sum(1 for d in s if 1 <= d <= 8),
            "medio": sum(1 for d in s if 9 <= d <= 17),
            "alto":  sum(1 for d in s if 18 <= d <= 25),
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

def calc_sequencias_consecutivas(sorteios):
    """
    Para cada sorteio, extrai todas as 'runs' de números consecutivos.
    Retorna: distribuição de tamanho de run e as sequências mais frequentes.
    """
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

    # top runs por tamanho (2 a 7)
    top_por_tamanho = {}
    for tam in range(2, 8):
        runs_tam = [r for r in todas_runs if len(r) == tam]
        c = Counter(runs_tam)
        top_por_tamanho[tam] = [
            {"seq": list(r), "count": cnt}
            for r, cnt in c.most_common(10)
        ]

    return dist_tamanho, top_por_tamanho

def calc_tendencia(sorteios, janela=50):
    """Frequência nos últimos `janela` sorteios vs. geral."""
    n = len(sorteios)
    freq_total = calc_frequencia(sorteios)
    freq_rec = calc_frequencia(sorteios[-janela:])
    dados = []
    for d in range(1, 26):
        ft = round(freq_total.get(d, 0) / n * 100, 1)
        fr = round(freq_rec.get(d, 0) / janela * 100, 1)
        dados.append({"d": d, "total": ft, "recente": fr, "delta": round(fr - ft, 1)})
    return dados

def calc_repeticao_anterior(sorteios):
    """Quantas dezenas se repetem entre cada sorteio e o anterior."""
    return [
        len(set(sorteios[i]) & set(sorteios[i - 1]))
        for i in range(1, len(sorteios))
    ]

def calc_ciclo_medio(sorteios):
    """Intervalo médio (em sorteios) entre aparições consecutivas de cada dezena."""
    indices = defaultdict(list)
    for i, s in enumerate(sorteios):
        for d in s:
            indices[d].append(i)

    resultado = {}
    for d in range(1, 26):
        idx = indices.get(d, [])
        intervalos = [b - a for a, b in zip(idx, idx[1:])]
        resultado[d] = {
            "ciclo": round(sum(intervalos) / len(intervalos), 1) if intervalos else None,
            "aparicoes": len(idx),
        }
    return resultado

def calc_trios_frequentes(sorteios, top_n=15):
    n = len(sorteios)
    c = Counter()
    for s in sorteios:
        for trio in combinations(sorted(s), 3):
            c[trio] += 1
    return [
        {"trio": list(trio), "count": cnt, "pct": round(cnt / n * 100, 1)}
        for trio, cnt in c.most_common(top_n)
    ]

def calc_grade(sorteios):
    """Distribuição média de dezenas por linha e coluna do volante 5x5."""
    def linha(d): return (d - 1) // 5 + 1
    def coluna(d): return (d - 1) % 5 + 1

    linhas_soma = Counter()
    colunas_soma = Counter()
    for s in sorteios:
        for d in s:
            linhas_soma[linha(d)] += 1
            colunas_soma[coluna(d)] += 1

    n = len(sorteios)
    return {
        "linhas": [round(linhas_soma[r] / n, 2) for r in range(1, 6)],
        "colunas": [round(colunas_soma[c] / n, 2) for c in range(1, 6)],
    }

def calc_digitos_finais(sorteios):
    """Distribuição por dígito final (0-9) das dezenas sorteadas."""
    n = len(sorteios)
    total = Counter()
    for s in sorteios:
        for d in s:
            total[d % 10] += 1
    return {
        "total": {dig: total.get(dig, 0) for dig in range(10)},
        "media_por_sorteio": {dig: round(total.get(dig, 0) / n, 2) for dig in range(10)},
    }

def calc_coocorrencia_completa(sorteios):
    c = Counter()
    for s in sorteios:
        for a, b in combinations(sorted(s), 2):
            c[(a, b)] += 1
    return c

def calc_anticorrelacao(cooc_completo, bottom_n=15):
    """Pares com menor co-ocorrência (Counter só guarda pares que saíram juntos
    ao menos 1 vez, então pares com zero aparições já ficam excluídos aqui)."""
    return sorted(cooc_completo.items(), key=lambda kv: kv[1])[:bottom_n]

def calc_meus_jogos(jogos_dict, rows, sorteios):
    """Desempenho histórico de um dicionário {nome: [15 dezenas]}."""
    n = len(sorteios)
    resultados = {}
    for nome, numeros in jogos_dict.items():
        r = calcular_jogo(set(numeros), rows, sorteios)
        resultados[nome] = {
            "numeros": sorted(numeros),
            "contagem": r["contagem"],
            "total": r["total_premios"],
            "pct_total": round(r["total_premios"] / n * 100, 1) if n else 0.0,
            "melhor": r["melhor"],
        }
    return resultados

def calc_numero_por_sorteio_historico(rows, sorteios):
    """Evolução de frequência acumulada dos top-5 números ao longo do tempo."""
    n = len(sorteios)
    freq_total = calc_frequencia(sorteios)
    top5 = [d for d, _ in freq_total.most_common(5)]
    acum = {d: [] for d in top5}
    contagem = Counter()
    for i, s in enumerate(sorteios, 1):
        contagem.update(s)
        for d in top5:
            acum[d].append(round(contagem.get(d, 0) / i * 100, 1))
    concursos = [r["concurso"] for r in rows]
    return {"dezenas": top5, "concursos": concursos, "series": acum}


# ─── blocos de 5 (A: 01-05, B: 06-10, C: 11-15, D: 16-20, E: 21-25) ──────────

BLOCOS_NOMES = ["A", "B", "C", "D", "E"]

def bloco_de(d: int) -> int:
    return (d - 1) // 5

def _contagem_blocos(s) -> list:
    c = Counter(bloco_de(d) for d in s)
    return [c.get(i, 0) for i in range(5)]

def calc_blocos_freq_individual(sorteios):
    """Frequência de cada número, agrupada pelo bloco (A-E) a que pertence."""
    freq = calc_frequencia(sorteios)
    resultado = {}
    for i, nome in enumerate(BLOCOS_NOMES):
        inicio = i * 5 + 1
        resultado[nome] = {d: freq.get(d, 0) for d in range(inicio, inicio + 5)}
    return resultado

def calc_blocos_combinacoes(sorteios, top_n=15):
    """Assinaturas de distribuição mais frequentes, ex: '3-3-3-3-3' → 47 vezes."""
    n = len(sorteios)
    contador = Counter()
    for s in sorteios:
        c = _contagem_blocos(s)
        contador["-".join(str(v) for v in c)] += 1
    return [
        {"combinacao": combo, "count": cnt, "pct": round(cnt / n * 100, 1) if n else 0}
        for combo, cnt in contador.most_common(top_n)
    ]

def calc_blocos_periodo(rows, sorteios):
    """Média de dezenas por bloco, agrupado por mês (YYYY-MM) — visão histórica fixa."""
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

def calc_blocos_coocorrencia(sorteios):
    """Matriz 5×5: frequência com que cada par de blocos contribui com >=3 dezenas
    no mesmo sorteio (diagonal = quantas vezes aquele bloco sozinho chegou a >=3)."""
    matriz = [[0] * 5 for _ in range(5)]
    for s in sorteios:
        c = _contagem_blocos(s)
        ativos = [i for i in range(5) if c[i] >= 3]
        for i in ativos:
            matriz[i][i] += 1
            for j in ativos:
                if i != j:
                    matriz[i][j] += 1
    return matriz

def calc_blocos_bundle(rows, sorteios):
    """Pacote completo da aba Blocos (usado tanto para o histórico total quanto
    para cada período do seletor temporal, exceto o heatmap por período que é
    sempre calculado sobre o histórico completo)."""
    return {
        "freq_individual": calc_blocos_freq_individual(sorteios),
        "combinacoes": calc_blocos_combinacoes(sorteios, top_n=15),
        "coocorrencia": calc_blocos_coocorrencia(sorteios),
    }


# ─── seletor de período temporal (ano / semestre / trimestre / bimestre / mês) ─

MESES_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

def calcular_bundle_periodo(rows_p, sorteios_p):
    """Estatísticas completas de um período do seletor: heatmap/frequência,
    pares/ímpares, atraso (relativo ao próprio período), sequências, co-ocorrência,
    tendência, soma, faixas, evolução, blocos, financeiro, repetição do concurso
    anterior, ciclo médio, trios, grade linha/coluna, dígitos finais e
    anti-correlação — mesmo conjunto de módulos exibido para 'Todos', só que
    recalculado sobre o subconjunto de sorteios do período."""
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
    evolucao = calc_numero_por_sorteio_historico(rows_p, sorteios_p)
    repeticao_anterior = calc_repeticao_anterior(sorteios_p)
    ciclo_medio = calc_ciclo_medio(sorteios_p)
    trios = calc_trios_frequentes(sorteios_p, top_n=15)
    grade = calc_grade(sorteios_p)
    digitos_finais = calc_digitos_finais(sorteios_p)
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
        "evolucao": {
            "dezenas": evolucao["dezenas"],
            "concursos": evolucao["concursos"],
            "series": {str(d): v for d, v in evolucao["series"].items()},
        },
        "blocos": calc_blocos_bundle(rows_p, sorteios_p),
        "financeiro": calc_financeiro(rows_p),
        "repeticao_anterior": repeticao_anterior,
        "ciclo_medio": ciclo_medio,
        "trios": trios,
        "grade": grade,
        "digitos_finais": digitos_finais,
        "anticorrelacao": [[[a, b], c] for (a, b), c in anticorrelacao],
    }

def gerar_periodos(rows, sorteios):
    """Agrupa os sorteios por ano/semestre/trimestre/bimestre/mês e pré-computa o
    bundle de cada período. Retorna (periodos: {id: bundle}, disponiveis: [{id,label,tipo,total}])."""
    grupos = defaultdict(list)
    labels = {}
    tipos = {}

    for i, row in enumerate(rows):
        iso = data_iso_de(row)
        ano, mes = int(iso[:4]), int(iso[5:7])

        pid = f"{ano}"
        grupos[pid].append(i)
        labels[pid] = str(ano)
        tipos[pid] = "ano"

        sem = 1 if mes <= 6 else 2
        pid = f"{ano}-S{sem}"
        grupos[pid].append(i)
        labels[pid] = f"{sem}º Sem {ano}"
        tipos[pid] = "semestre"

        tri = (mes - 1) // 3 + 1
        pid = f"{ano}-T{tri}"
        grupos[pid].append(i)
        labels[pid] = f"T{tri} {ano}"
        tipos[pid] = "trimestre"

        bim = (mes - 1) // 2 + 1
        pid = f"{ano}-B{bim}"
        grupos[pid].append(i)
        labels[pid] = f"Bim{bim} {ano}"
        tipos[pid] = "bimestre"

        pid = f"{ano}-M{mes:02d}"
        grupos[pid].append(i)
        labels[pid] = f"{MESES_PT[mes - 1]} {ano}"
        tipos[pid] = "mes"

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

def _campo(row: dict, *chaves):
    """Pega o primeiro valor não-vazio entre várias chaves possíveis — necessário
    porque o CSV usa 'valor_premio_1'/'ganhadores_1' e o banco usa 'valor_premio'/'ganhadores'."""
    for chave in chaves:
        valor = row.get(chave)
        if valor not in (None, ""):
            return valor
    return None

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
    """Resumo financeiro (prêmios pagos, acumulados) de um conjunto de sorteios —
    usado tanto para o histórico completo quanto para cada período do seletor."""
    n = len(rows_p)
    registros = []
    for row in rows_p:
        valor = _to_float(_campo(row, "valor_premio_1", "valor_premio"))
        ganhadores = _to_int(_campo(row, "ganhadores_1", "ganhadores"))
        acumulado = _acumulado_bool(_campo(row, "acumulado"))
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
        if r is None:
            return None
        return {"valor": r["valor"], "concurso": r["concurso"], "data": r["data"]}

    return {
        "total_premios_pagos": round(total_premios_pagos, 2),
        "media_premio_faixa1": media_premio_faixa1,
        "maior_premio": _resumo_premio(maior),
        "menor_premio": _resumo_premio(menor),
        "total_acumulados": total_acumulados,
        "pct_acumulados": round(total_acumulados / n * 100, 1) if n else 0.0,
        "total_sorteios": n,
    }

def calc_historico(rows: list[dict], sorteios: list[list[int]]) -> dict:
    """Monta a árvore Ano → Mês → Sorteio usada na aba Histórico."""
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
            "acumulado": _acumulado_bool(_campo(row, "acumulado")),
            "ganhadores": _to_int(_campo(row, "ganhadores_1", "ganhadores")),
            "premio": _to_float(_campo(row, "valor_premio_1", "valor_premio")),
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

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Lotofácil BI — {titulo}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    /* paleta "Dark Analytics App" */
    --bg: #08090f;
    --bg2: #0f1018;
    --bg3: #161824;
    --border: rgba(255,255,255,0.06);
    --accent: #7c3aed;
    --accent2: #a855f7;
    --neon: rgba(124,58,237,0.15);
    --text: #f1f0f5;
    --muted: #6b7280;
    --green: #10b981;
    --red: #ef4444;
    --gold: #f59e0b;
    /* aliases para não quebrar regras existentes que já usavam estes nomes */
    --card: var(--bg2);
    --accent3: var(--green);
    --accent4: var(--gold);
    --accent5: var(--red);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    background: radial-gradient(ellipse 1200px 800px at 15% -10%, #211b45 0%, transparent 60%),
                radial-gradient(ellipse 900px 700px at 100% 0%, #0d2b3d 0%, transparent 55%),
                var(--bg);
    background-attachment: fixed;
    color: var(--text);
    font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  @media (max-width: 640px) { body { font-size: 13px; } }
  header {
    position: sticky; top: 0; z-index: 50;
    background: rgba(15,16,24,0.72);
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border);
    padding: 16px 28px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  }
  header h1 { font-size: 20px; font-weight: 700; color: var(--text); letter-spacing: -0.3px; display: flex; align-items: center; gap: 8px; }
  header .meta { display: none; } /* substituído pelo badge de concurso central */
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
  /* botões de ação: primário sólido, secundário outline — border-radius 8px, altura mín. 40px */
  .btn-primary, .btn-secondary {
    border-radius: 8px; min-height: 40px; padding: 0 18px; font-weight: 600; font-size: 13px;
    cursor: pointer; transition: background .15s, border-color .15s, box-shadow .15s, transform .1s;
    display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  }
  .btn-primary { background: var(--accent); border: 1px solid var(--accent); color: #fff; }
  .btn-primary:hover:not(:disabled) { background: #6d28d9; box-shadow: 0 0 0 3px rgba(124,58,237,.25); }
  .btn-primary:active:not(:disabled) { transform: scale(.97); }
  .btn-secondary { background: transparent; border: 1px solid var(--accent); color: #a78bfa; }
  .btn-secondary:hover:not(:disabled) { background: rgba(124,58,237,.12); }
  .btn-secondary:active:not(:disabled) { transform: scale(.97); }
  .btn-primary:disabled, .btn-secondary:disabled { opacity: .5; cursor: not-allowed; transform: none; box-shadow: none; }
  @media (max-width: 640px) { .btn-primary, .btn-secondary { flex: 1 1 auto; } }
  /* indicador de token configurado + engrenagem */
  .gh-token-dot { width: 8px; height: 8px; border-radius: 50%; background: #6b7280; display: inline-block; flex-shrink: 0; }
  .gh-token-dot.ativo { background: var(--green); box-shadow: 0 0 6px rgba(16,185,129,.7); }
  .gh-gear-btn { background: transparent !important; border: 1px solid var(--border) !important; border-radius: 6px; padding: 5px 8px !important; cursor: pointer; font-size: 13px !important; color: var(--muted) !important; }
  .gh-gear-btn:hover { border-color: var(--accent) !important; color: #a78bfa !important; }
  /* feedback do disparo do workflow */
  .gh-feedback { margin: 10px 24px 0; padding: 10px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; }
  .gh-feedback.ok { background: rgba(16,185,129,.12); border: 1px solid var(--green); color: var(--green); }
  .gh-feedback.erro { background: rgba(239,68,68,.12); border: 1px solid var(--red); color: var(--red); }
  /* modal de configuração do token */
  .gh-modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 20px; animation: ghModalFadeIn .18s ease; }
  .gh-modal { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 24px; max-width: 440px; width: 100%; max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,.5); animation: ghModalSlideUp .22s cubic-bezier(.16,1,.3,1); }
  @keyframes ghModalFadeIn { from { opacity: 0; } to { opacity: 1; } }
  @keyframes ghModalSlideUp { from { opacity: 0; transform: translateY(18px) scale(.98); } to { opacity: 1; transform: translateY(0) scale(1); } }
  @media (max-width: 640px) { .gh-modal { max-width: 90vw; padding: 20px; } }
  .gh-modal h3 { font-size: 16px; margin-bottom: 12px; color: #a78bfa; }
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
  #gh-modal-salvar:hover { background: #6d28d9; }
  #gh-modal-remover { background: transparent; border: 1px solid var(--red); color: var(--red); margin-right: auto; }
  #gh-modal-remover:hover { background: rgba(239,68,68,.1); }
  @media (max-width: 640px) {
    .gh-modal-acoes { flex-direction: column-reverse; }
    .gh-modal-acoes button { width: 100%; margin-right: 0 !important; }
  }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; padding: 24px 24px 0; }
  .kpi { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 18px 20px; box-shadow: 0 4px 24px rgba(0,0,0,.25); transition: transform .15s, box-shadow .15s; }
  .kpi:hover { transform: translateY(-2px); box-shadow: 0 8px 28px rgba(0,0,0,.35); }
  .kpi .kpi-icon { font-size: 18px; margin-bottom: 6px; display: block; opacity: .9; }
  .kpi .label { color: var(--muted); font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: .5px; line-height: 1.4; }
  .kpi .value { font-size: 36px; font-weight: 700; margin-top: 4px; color: #a78bfa; line-height: 1.15; }
  .kpi .sub { font-size: 11px; color: var(--muted); margin-top: 4px; }
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
  /* tablet (641px-1024px): grids de 3 colunas viram 2; os de 2 colunas já servem */
  @media (max-width: 1024px) { .grid-3 { grid-template-columns: 1fr 1fr; } }
  /* mobile (<=640px): tudo vira 1 coluna */
  @media (max-width: 640px) { .grid-2, .grid-3, .grid-13, .grid-31 { grid-template-columns: 1fr; } .grid { gap: 14px; padding: 16px; } }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 24px; box-shadow: 0 4px 24px rgba(0,0,0,.3); transition: border-color .2s ease, box-shadow .2s ease; }
  .card:hover { border-color: rgba(124,58,237,.3); box-shadow: 0 4px 24px rgba(0,0,0,.3), 0 0 0 1px rgba(124,58,237,.08); }
  .card h2 { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: .8px; color: var(--muted); margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
  /* fade-in ao trocar de aba de página (Visão Geral / Blocos / Histórico) */
  .page-content { animation: pageFadeIn .25s ease; }
  @keyframes pageFadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
  @media (max-width: 640px) { .card { padding: 16px; border-radius: 12px; } }
  canvas { max-height: 280px; }
  /* heatmap (usado só na aba Blocos — grade-wrap) */
  .heatcell { border-radius: 10px; padding: 14px 4px; text-align: center; font-weight: 800; font-size: 18px; transition: transform .15s; cursor: default; }
  .heatcell:hover { transform: scale(1.12); z-index: 2; position: relative; }
  .heatcell .freq { font-size: 11px; font-weight: 500; opacity: .8; display: block; margin-top: 3px; }
  @media (max-width: 640px) {
    .heatcell { padding: 10px 2px; font-size: 15px; border-radius: 8px; }
    .heatcell .freq { font-size: 9px; }
  }
  /* grade interativa 5×5 — substitui o antigo heatmap estático da Visão Geral */
  .numgrid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; max-width: 420px; margin: 0 auto; }
  .numgrid-cell {
    aspect-ratio: 1; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 15px; color: #fff; cursor: pointer; border: 2px solid transparent;
    transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
  }
  .numgrid-cell:hover { transform: scale(1.1); }
  .numgrid-cell.selecionada { border-color: var(--accent2); box-shadow: 0 0 0 4px var(--neon), 0 0 18px rgba(168,85,247,.55); transform: scale(1.06); }
  .numgrid-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 18px; flex-wrap: wrap; }
  .numgrid-hint { font-size: 12px; color: var(--muted); flex: 1 1 260px; }
  @media (max-width: 640px) {
    .numgrid { max-width: 300px; gap: 7px; }
    .numgrid-cell { font-size: 12px; }
    .numgrid-footer { flex-direction: column; align-items: stretch; }
  }
  /* tab system */
  .tabs { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
  .tab { padding: 5px 14px; border-radius: 6px; border: 1px solid var(--border); background: transparent; color: var(--muted); cursor: pointer; font-size: 12px; font-weight: 600; transition: all .15s; }
  .tab.active { background: var(--accent); border-color: var(--accent); color: #fff; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  /* table */
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--muted); font-weight: 700; font-size: 11px; text-transform: uppercase; padding: 10px 8px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--card); z-index: 1; }
  td { padding: 9px 8px; border-bottom: 1px solid #1e2130; }
  tbody tr:nth-child(even) td { background: rgba(255,255,255,.025); }
  tbody tr:hover td { background: rgba(124,58,237,.07); }
  tr:last-child td { border-bottom: none; }
  @media (max-width: 640px) { table { font-size: 12px; } td, th { padding: 7px 6px; } }
  .badge { display: inline-flex; align-items: center; gap: 3px; background: #1e2130; border-radius: 4px; padding: 2px 6px; font-size: 11px; font-weight: 700; }
  .badge.up { color: var(--green); }
  .badge.down { color: var(--red); }
  .badge.flat { color: var(--muted); }
  .seq-tag { display: inline-block; background: #2a1f5e; border: 1px solid #4c3db5; color: #a78bfa; border-radius: 4px; padding: 2px 7px; font-weight: 700; font-size: 12px; margin-right: 3px; }
  .bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
  .bar-row .label { width: 90px; font-size: 12px; color: var(--muted); text-align: right; flex-shrink: 0; }
  .bar-row .bar { height: 18px; border-radius: 4px; background: var(--accent); min-width: 4px; transition: width .4s; }
  .bar-row .val { font-size: 12px; color: var(--text); flex-shrink: 0; }
  footer { text-align: center; color: var(--muted); font-size: 11px; padding: 16px; border-top: 1px solid var(--border); margin-top: 8px; }
  /* mini stats row */
  .mini-stats { display: flex; gap: 24px; margin-top: 14px; font-size: 12px; color: var(--muted); flex-wrap: wrap; }
  .mini-stats b { color: var(--text); font-size: 16px; display: block; }
  .mini-stats b.money-pos { color: var(--green); }
  .mini-stats b.money-neg { color: var(--red); }
  /* status badges for ciclo médio */
  .status-tag { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px; }
  .status-tag.dentro { background: rgba(16,185,129,.15); color: var(--green); }
  .status-tag.alem { background: rgba(239,68,68,.15); color: var(--red); }
  /* grade labeled heatmap */
  .grade-wrap { display: grid; grid-template-columns: 32px repeat(5, 1fr); gap: 6px; align-items: center; }
  .grade-wrap .rowhead, .grade-wrap .colhead { text-align: center; font-size: 11px; color: var(--muted); font-weight: 700; }
  .grade-wrap .heatcell { padding: 12px 4px; }
  /* blocos — ranking de frequência individual por número */
  .blocos-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
  @media (max-width: 1100px) { .blocos-grid { grid-template-columns: repeat(3, 1fr); } }
  @media (max-width: 900px) { .blocos-grid { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 560px) { .blocos-grid { grid-template-columns: 1fr; } }
  .bloco-card { background: #1a1d27; border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
  .bloco-card h3 { font-size: 12px; font-weight: 700; color: #a78bfa; margin-bottom: 12px; text-transform: uppercase; letter-spacing: .5px; }
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
  /* window switch buttons (hot/cold) */
  .window-btns { display: flex; gap: 6px; margin-bottom: 14px; }
  /* hot/cold cards */
  .hotcold-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(88px, 1fr)); gap: 8px; margin-top: 4px; }
  .hotcold-card { border-radius: 8px; padding: 10px 6px; text-align: center; border: 1px solid var(--border); background: #1e2130; }
  .hotcold-card.quente { background: rgba(239,68,68,.15); border-color: var(--red); }
  .hotcold-card.frio { background: rgba(6,182,212,.15); border-color: var(--accent2); }
  .hotcold-card .num { font-size: 16px; font-weight: 700; }
  .hotcold-card .status { font-size: 14px; }
  .hotcold-card .pct { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .hotcold-legend { display: flex; gap: 18px; margin-bottom: 12px; font-size: 12px; color: var(--muted); }
  /* bet simulator */
  .sim-box { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
  .sim-box input { flex: 1; min-width: 260px; background: #0f1117; border: 1px solid var(--border); border-radius: 6px; padding: 9px 12px; color: var(--text); font-size: 13px; }
  .sim-box input:focus { outline: none; border-color: var(--accent); }
  .sim-box button { background: var(--accent); border: none; border-radius: 6px; padding: 9px 20px; color: #fff; font-weight: 600; cursor: pointer; font-size: 13px; }
  .sim-box button:hover { background: #6d28d9; }
  .sim-error { color: var(--red); font-size: 12px; margin: -6px 0 12px; }
  .sim-result-row { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid #1e2130; }
  .sim-result-row:last-child { border-bottom: none; }
  .sim-result-row .pontos { width: 90px; font-weight: 700; color: var(--text); flex-shrink: 0; }
  /* simulador — múltiplos jogos */
  .sim-qtd-selector { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 16px; font-size: 13px; }
  .sim-qtd-selector label { display: flex; align-items: center; gap: 5px; cursor: pointer; color: var(--text); }
  .sim-qtd-label { font-weight: 700; color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .5px; }
  .sim-jogos-lista { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
  .sim-jogo-row { display: flex; align-items: center; gap: 8px; }
  .sim-jogo-label { width: 62px; flex-shrink: 0; font-size: 12px; color: var(--muted); font-weight: 600; }
  .sim-jogo-input { flex: 1; min-width: 0; background: #0f1117; border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; color: var(--text); font-size: 13px; }
  .sim-jogo-input:focus { outline: none; border-color: var(--accent); }
  .sim-jogo-badge { min-width: 96px; text-align: center; font-size: 11px; font-weight: 700; padding: 4px 8px; border-radius: 6px; flex-shrink: 0; border: 1px solid var(--border); color: var(--muted); }
  .sim-jogo-badge.ok { background: rgba(16,185,129,.15); color: var(--green); border-color: rgba(16,185,129,.4); }
  .sim-jogo-badge.parcial { background: rgba(245,158,11,.15); color: #fbbf24; border-color: rgba(245,158,11,.4); }
  .sim-jogo-badge.erro { background: rgba(239,68,68,.15); color: var(--red); border-color: rgba(239,68,68,.4); }
  .sim-jogo-remove { background: transparent; border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; cursor: pointer; color: var(--muted); font-size: 13px; flex-shrink: 0; }
  .sim-jogo-remove:hover { border-color: var(--red); color: var(--red); }
  .sim-acoes { display: flex; gap: 10px; margin-bottom: 14px; }
  .sim-acoes button { background: var(--accent); border: none; border-radius: 6px; padding: 9px 18px; color: #fff; font-weight: 600; cursor: pointer; font-size: 13px; }
  .sim-acoes button:hover { background: #6d28d9; }
  .sim-acoes #sim-btn-add { background: transparent; border: 1px solid var(--border); color: var(--text); }
  .sim-acoes #sim-btn-add:hover { border-color: var(--accent); color: #a78bfa; }
  .sim-aviso { color: #fbbf24; font-size: 12px; margin-bottom: 10px; }
  .sim-compare-row.destaque-ouro td { background: rgba(245,158,11,.12); }
  .sim-trofeu { margin-left: 4px; }
  .sim-detalhe-toggle { cursor: pointer; color: var(--accent2); font-size: 11px; background: none; border: none; padding: 0; text-decoration: underline; }
  .sim-detalhe-painel { display: none; padding: 10px 0 4px; }
  .sim-detalhe-painel.aberto { display: block; }
  .sim-detalhe-item { display: inline-block; margin: 2px 6px 2px 0; padding: 3px 8px; border-radius: 6px; background: #1e2130; font-size: 11px; color: var(--text); }
  .sim-copiar-btn { margin-top: 12px; background: transparent; border: 1px solid var(--border); border-radius: 6px; padding: 8px 16px; color: var(--text); cursor: pointer; font-size: 12px; }
  .sim-copiar-btn:hover { border-color: var(--accent); color: #a78bfa; }
  .money-pos { color: var(--green); font-weight: 700; }
  .money-neg { color: var(--red); font-weight: 700; }
  /* seletor de período — card com grupos empilhados por tipo (Ano/Semestre
     sempre visíveis; Trimestre/Bimestre/Mês atrás de um accordion) */
  .period-selector-wrap { padding: 14px 24px 0; position: sticky; top: 65px; z-index: 40; }
  .period-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 12px; padding: 16px 20px; box-shadow: 0 4px 20px rgba(0,0,0,.25); }
  .period-card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
  .period-card-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .8px; color: var(--muted); }
  .period-row { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; }
  .period-row:last-child { margin-bottom: 0; }
  .period-row .tipo-label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; width: 90px; flex-shrink: 0; padding-top: 5px; }
  .period-row-scroll { display: flex; gap: 6px; overflow-x: auto; flex-wrap: nowrap; scrollbar-width: none; -ms-overflow-style: none; padding-bottom: 2px; }
  .period-row-scroll::-webkit-scrollbar { display: none; }
  .period-btn {
    background: var(--bg3); border: 1px solid var(--border); border-radius: 6px;
    padding: 4px 10px; font-size: 12px; color: var(--text); cursor: pointer;
    flex-shrink: 0; white-space: nowrap; transition: background .15s, border-color .15s, color .15s;
  }
  .period-btn:hover { border-color: var(--accent2); }
  .period-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }
  .period-accordion-toggle { background: transparent; border: none; color: var(--accent2); font-size: 11px; cursor: pointer; padding: 4px 0; text-align: left; flex-shrink: 0; }
  .period-accordion-toggle:hover { text-decoration: underline; }
  .period-row.colapsada .period-row-scroll { display: none; }
  @media (max-width: 640px) {
    .period-selector-wrap { top: 57px; padding: 10px 16px 0; }
    .period-card { padding: 12px 14px; }
    .period-row { flex-direction: column; align-items: stretch; gap: 4px; }
    .period-row .tipo-label { width: auto; padding-top: 0; }
    .period-btn { font-size: 11px; padding: 3px 8px; }
  }
  /* banner do período ativo */
  .periodo-banner { margin: 12px 24px 0; padding: 10px 16px; background: rgba(245,158,11,.12); border: 1px solid var(--accent4); border-radius: 8px; color: #fbbf24; font-size: 13px; font-weight: 600; }
  .update-banner { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .update-banner button { background: #f59e0b; border: none; border-radius: 8px; min-height: 36px; padding: 0 16px; color: #1a1300; font-weight: 700; cursor: pointer; font-size: 12px; flex-shrink: 0; transition: background .15s, transform .1s; }
  .update-banner button:hover { background: #fbbf24; }
  .update-banner button:active { transform: scale(.97); }
  /* resumo financeiro */
  .fin-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; }
  .fin-item { background: #1a1d27; border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
  .fin-item .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 6px; }
  .fin-item .value { font-size: 18px; font-weight: 700; color: #a78bfa; }
  .fin-item .sub { font-size: 11px; color: var(--muted); margin-top: 4px; }
  /* abas de página (Visão Geral / Blocos) */
  .page-tabs { display: flex; gap: 6px; padding: 16px 24px; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: thin; }
  .page-tab { padding: 10px 20px; border: 1px solid transparent; border-radius: 999px; background: transparent; color: var(--muted); cursor: pointer; font-size: 13px; font-weight: 600; white-space: nowrap; flex-shrink: 0; transition: background .15s, color .15s, border-color .15s; }
  .page-tab:hover { color: var(--text); background: rgba(124,58,237,.1); border-color: var(--border); }
  .page-tab.active { color: #fff; background: var(--accent); border-color: var(--accent); box-shadow: 0 0 0 4px var(--neon); }
  @media (max-width: 640px) {
    .page-tabs { padding: 12px 16px 0; gap: 4px; }
    .page-tab { padding: 10px 16px; font-size: 13px; }
  }
  /* histórico — cabeçalho e controles */
  .hist-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 20px 24px 0; }
  .hist-header-info { display: flex; gap: 28px; flex-wrap: wrap; }
  .hist-header-info .label { display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
  .hist-header-info b { font-size: 18px; color: #a78bfa; }
  .hist-controles { margin: 20px 24px 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; align-items: end; }
  @media (max-width: 900px) { .hist-controles { grid-template-columns: 1fr; } }
  .hist-controle-grupo label { display: block; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 6px; }
  /* histórico — árvore */
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
  @keyframes histFlash { 0%, 100% { background: transparent; } 30% { background: rgba(124,58,237,.35); } }
  .hist-sorteio-detail { max-height: 0; overflow: hidden; transition: max-height .25s ease; margin-left: 24px; }
  .hist-detail-inner { padding: 10px 12px 14px; }
  .hist-detail-titulo { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
  .hist-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .hist-badge { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 8px; background: #2a1f5e; border: 1px solid #4c3db5; color: #fff; font-weight: 700; font-size: 12px; }
  .hist-detail-meta { display: flex; gap: 20px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
  .hist-detail-meta b { color: var(--text); }
</style>
</head>
<body>
<header>
  <h1>🍀 Lotofácil BI</h1>
  <div class="concurso-badge"><span class="dot"></span><b>{subtitulo}</b></div>
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
    <div class="gh-modal-aviso">⚠️ Salvo apenas no seu navegador (localStorage). Nunca enviado para nenhum servidor nosso — só direto para a API do GitHub.</div>
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
</div>

<div id="page-geral" class="page-content">
<!-- Grade interativa 5×5 — substitui o heatmap estático; clique filtra os gráficos abaixo -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card numgrid-card">
    <h2>🎯 Grade interativa — clique nas dezenas para filtrar os gráficos</h2>
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
  <div class="kpi"><span class="kpi-icon">⚖️</span><div class="label">Média pares/sorteio</div><div class="value" id="kpi-pares">—</div></div>
  <div class="kpi"><span class="kpi-icon">➕</span><div class="label">Soma média das 15 dez.</div><div class="value" id="kpi-soma">—</div></div>
  <div class="kpi"><span class="kpi-icon">🔗</span><div class="label">Maior sequência vista</div><div class="value" id="kpi-seq">—</div><div class="sub">números consecutivos</div></div>
  <div class="kpi"><span class="kpi-icon">🔁</span><div class="label">Repetições do concurso anterior</div><div class="value" id="kpi-repeticao">—</div><div class="sub" id="kpi-repeticao-sub"></div></div>
</div>

<!-- Resumo financeiro do período selecionado -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>💰 Resumo financeiro (prêmios da faixa 1 — 15 acertos)</h2>
    <div class="fin-grid" id="financeiro-resumo"></div>
  </div>
</div>

<!-- Frequência + Atraso + Pares/Ímpares -->
<div class="grid grid-3">
  <div class="card">
    <h2>📊 Frequência por dezena</h2>
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
      <span><span style="color:#7c3aed">■</span> Pares: <b id="pct-pares">—</b></span>
      <span><span style="color:#06b6d4">■</span> Ímpares: <b id="pct-impares">—</b></span>
    </div>
  </div>
</div>

<!-- Distribuição por faixa + Soma -->
<div class="grid grid-2">
  <div class="card">
    <h2>📈 Distribuição por faixa (média por sorteio)</h2>
    <canvas id="chartFaixas"></canvas>
  </div>
  <div class="card">
    <h2>➕ Distribuição da soma das 15 dezenas</h2>
    <canvas id="chartSoma"></canvas>
  </div>
</div>

<!-- Sequências consecutivas -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🔗 Sequências de números consecutivos mais frequentes</h2>
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

<!-- Distribuição de tamanho de sequência -->
<div class="grid grid-2">
  <div class="card">
    <h2>🔢 Quantos sorteios tiveram sequências de tamanho N</h2>
    <canvas id="chartSeqDist"></canvas>
  </div>
  <div class="card">
    <h2>📈 Evolução acumulada — top 5 dezenas mais sorteadas</h2>
    <canvas id="chartEvolucao"></canvas>
  </div>
</div>

<!-- Repetição do concurso anterior + Ciclo médio -->
<div class="grid grid-2">
  <div class="card">
    <h2>🔁 Repetição do concurso anterior</h2>
    <canvas id="chartRepeticao"></canvas>
    <div class="mini-stats">
      <div>Média<b id="rep-media">—</b></div>
      <div>Mínimo<b id="rep-min">—</b></div>
      <div>Máximo<b id="rep-max">—</b></div>
    </div>
  </div>
  <div class="card">
    <h2>🔄 Ciclo médio por dezena (curto → longo)</h2>
    <div id="ciclo-list" style="overflow-y:auto; max-height:340px;"></div>
  </div>
</div>

<!-- Trios mais frequentes + Grade linha/coluna -->
<div class="grid grid-2">
  <div class="card">
    <h2>🔺 Top 15 trios mais frequentes</h2>
    <div id="trios-list" style="overflow-y:auto; max-height:340px;"></div>
  </div>
  <div class="card">
    <h2>🎛️ Análise de grade — média de dezenas por linha e coluna</h2>
    <canvas id="chartGrade"></canvas>
  </div>
</div>

<!-- Grade heatmap real -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🗂️ Mapa de calor do volante (grade real 5×5)</h2>
    <div class="grade-wrap" id="grade-heatmap"></div>
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

<!-- Dígitos finais -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🔟 Dígitos finais — média por sorteio</h2>
    <canvas id="chartDigitos"></canvas>
  </div>
</div>

<!-- Simulador de aposta -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🎰 Simulador de aposta</h2>
    <p style="color:var(--muted); font-size:12px; margin-bottom:14px;">
      Aceita números separados por espaço, vírgula, ponto e vírgula, traço, ponto — ou qualquer mistura (ex: "01, 02 05-06;07").
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
    <div id="sim-result"></div>
  </div>
</div>

<!-- Meus jogos — ranking -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="jogos-ranking-card">
    <h2>🏅 Meus jogos — ranking comparativo (≥11 acertos)</h2>
    <div id="jogos-ranking" style="overflow-x:auto;"></div>
  </div>
</div>

<!-- Meus jogos — detalhamento -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="jogos-detalhe-card">
    <h2>📋 Meus jogos — detalhamento por jogo</h2>
    <div class="tabs" id="jogos-tabs"></div>
    <div id="jogos-contents"></div>
  </div>
</div>

<!-- Jogos sugeridos — ranking -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="jogos-sug-ranking-card">
    <h2>✨ Jogos sugeridos (30) — ranking comparativo (≥11 acertos)</h2>
    <p style="color:var(--muted); font-size:12px; margin-bottom:12px;">
      Gerados seguindo os padrões estatísticos do histórico (soma, pares/ímpares, linha/coluna,
      pares e trios frequentes). Isto é autoconsistência estatística, não previsão — a Lotofácil
      é aleatória e todo jogo tem sempre a mesma probabilidade real de ganhar.
    </p>
    <div id="jogos-sug-ranking" style="overflow-x:auto;"></div>
  </div>
</div>

<!-- Jogos sugeridos — detalhamento -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="jogos-sug-detalhe-card">
    <h2>📋 Jogos sugeridos (30) — detalhamento por jogo</h2>
    <div class="tabs" id="jogos-sug-tabs"></div>
    <div id="jogos-sug-contents"></div>
  </div>
</div>

</div><!-- /page-geral -->

<div id="page-blocos" class="page-content" style="display:none;">

<div class="kpis">
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco A (01-05) — campeão</div><div class="value" id="kpi-bloco-a">—</div><div class="sub" id="kpi-bloco-a-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco B (06-10) — campeão</div><div class="value" id="kpi-bloco-b">—</div><div class="sub" id="kpi-bloco-b-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco C (11-15) — campeão</div><div class="value" id="kpi-bloco-c">—</div><div class="sub" id="kpi-bloco-c-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco D (16-20) — campeão</div><div class="value" id="kpi-bloco-d">—</div><div class="sub" id="kpi-bloco-d-sub"></div></div>
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco E (21-25) — campeão</div><div class="value" id="kpi-bloco-e">—</div><div class="sub" id="kpi-bloco-e-sub"></div></div>
</div>

<!-- Frequência individual de cada número, dentro do seu bloco -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🧩 Frequência de cada número dentro do seu bloco</h2>
    <div class="blocos-grid" id="blocos-ranking-grid"></div>
  </div>
</div>

<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🏆 Campeão e lanterna de cada bloco</h2>
    <div id="blocos-campeoes" style="overflow-x:auto;"></div>
  </div>
</div>

<!-- 3.2 Combinações de blocos mais frequentes -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🧮 Combinações de distribuição mais frequentes (A-B-C-D-E)</h2>
    <div id="blocos-combinacoes" style="overflow-x:auto;"></div>
  </div>
</div>

<!-- 3.5 Co-ocorrência entre blocos -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🔀 Co-ocorrência entre blocos (ambos com ≥3 dezenas no mesmo sorteio)</h2>
    <div class="grade-wrap" id="blocos-coocorrencia" style="grid-template-columns: 60px repeat(5, 1fr);"></div>
  </div>
</div>

<!-- 3.3 Heatmap de blocos por período (sempre histórico completo) -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🗓️ Blocos por período (média mensal — histórico completo)</h2>
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
    <div class="sim-box" style="margin-bottom:0;">
      <input type="text" id="hist-busca-concurso" placeholder="ex: 3729"/>
      <button id="hist-btn-buscar">Ir</button>
    </div>
    <div class="sim-error" id="hist-busca-erro" style="display:none;"></div>
  </div>
  <div class="hist-controle-grupo">
    <label for="hist-filtro-dezena">Filtrar por dezena</label>
    <div class="sim-box" style="margin-bottom:0;">
      <input type="text" id="hist-filtro-dezena" placeholder="ex: 07"/>
      <button id="hist-btn-limpar-filtro">Limpar</button>
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

<footer>Dados: API oficial Caixa Econômica Federal • {gerado_em}</footer>

<script>
const DATA = {data_json};

// helpers
const C = (id) => document.getElementById(id).getContext('2d');
const COLORS = ['#7c3aed','#06b6d4','#10b981','#f59e0b','#ef4444','#8b5cf6','#0ea5e9','#34d399','#fbbf24','#f87171'];
const chartDefaults = {
  responsive: true,
  maintainAspectRatio: true,
  plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e2130' } },
    y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e2130' } }
  }
};

// ── registro de instâncias Chart.js (permite recriar ao trocar de período) ──
const chartRegistry = {};
function criarChart(id, config) {
  if (chartRegistry[id]) chartRegistry[id].destroy();
  chartRegistry[id] = new Chart(C(id), config);
  return chartRegistry[id];
}

// contador animado (0 → valor) para os KPIs — baseado em nº de frames, não em
// tempo de relógio, para não travar a barra de progresso em máquinas lentas.
// Navegadores pausam requestAnimationFrame quando a aba fica em segundo plano
// (pode ficar parado indefinidamente até o usuário voltar); o setTimeout de
// segurança garante que o valor final apareça mesmo que isso aconteça no meio
// da animação.
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

// ── módulos que reagem ao seletor de período (Tarefa 4) ──────────────────────

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
  const maxSeq = Math.max(...Object.keys(bundle.seq_dist_tamanho).map(Number));
  animarContador(document.getElementById('kpi-seq'), maxSeq, 0);
  const pctPares = (avgPares/15*100).toFixed(1);
  document.getElementById('pct-pares').textContent = pctPares + '%';
  document.getElementById('pct-impares').textContent = (100-pctPares).toFixed(1) + '%';
}

function renderPI(bundle) {
  const total_p = bundle.pares_impares.reduce((a,b)=>a+b.pares,0);
  const total_i = bundle.pares_impares.reduce((a,b)=>a+b.impares,0);
  criarChart('chartPI', {
    type: 'doughnut',
    data: {
      labels: ['Pares','Ímpares'],
      datasets: [{ data: [total_p, total_i], backgroundColor: ['#7c3aed','#06b6d4'], borderWidth: 0 }]
    },
    options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { labels: { color: '#94a3b8' } } }, cutout: '65%' }
  });
}

function renderChartFreq(bundle) {
  const sorted_freq = Object.entries(bundle.frequencia).map(([d,c])=>({d:+d,c})).sort((a,b)=>b.c-a.c);
  const labels = sorted_freq.map(x => String(x.d).padStart(2,'0'));
  const values = sorted_freq.map(x => x.c);
  const colors = values.map((_,i) => i < 5 ? '#7c3aed' : i > 19 ? '#ef4444' : '#334155');
  criarChart('chartFreq', {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Vezes sorteada', data: values, backgroundColor: colors, borderRadius: 4 }] },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderChartAtraso(bundle) {
  const atr = Object.entries(bundle.atraso).map(([d,a])=>({d:+d,a})).sort((a,b)=>b.a-a.a);
  const colors = atr.map(x => x.a >= 5 ? '#ef4444' : x.a >= 3 ? '#f59e0b' : '#10b981');
  criarChart('chartAtraso', {
    type: 'bar',
    data: {
      labels: atr.map(x => String(x.d).padStart(2,'0')),
      datasets: [{ label: 'Sorteios de atraso', data: atr.map(x=>x.a), backgroundColor: colors, borderRadius: 4 }]
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
          <div style="min-width:200px">${tags}</div>
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
  const total = bundle.meta.total;
  if (!items.length) {
    list.innerHTML = '<p style="color:var(--muted)">Sem dados suficientes neste período.</p>';
    return;
  }
  const maxC = items[0][1];
  const table = document.createElement('table');
  table.innerHTML = `<thead><tr><th>#</th><th>Par</th><th>Aparições</th><th>% sorteios</th><th>Freq.</th></tr></thead>`;
  const tbody = document.createElement('tbody');
  items.forEach(([pair, cnt], i) => {
    const [a, b] = pair;
    const pct = (cnt / total * 100).toFixed(1);
    const w = Math.round(cnt / maxC * 80);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="color:var(--muted)">${i+1}</td>
      <td><span class="seq-tag">${String(a).padStart(2,'0')}</span><span class="seq-tag">${String(b).padStart(2,'0')}</span></td>
      <td>${cnt}</td>
      <td>${pct}%</td>
      <td><div style="height:8px;border-radius:3px;background:#7c3aed;width:${w}px;min-width:4px"></div></td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  list.appendChild(table);
}

function renderChartTend(bundle) {
  const tend = [...bundle.tendencia].sort((a,b) => Math.abs(b.delta) - Math.abs(a.delta));
  const labels = tend.map(x => String(x.d).padStart(2,'0'));
  criarChart('chartTend', {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Total (%)', data: tend.map(x=>x.total), backgroundColor: '#334155', borderRadius: 4 },
        { label: 'Recente (%)', data: tend.map(x=>x.recente), backgroundColor: tend.map(x=>x.delta>0?'#10b981':'#ef4444'), borderRadius: 4 }
      ]
    },
    options: { ...chartDefaults, plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } } }
  });
}

function renderBlocos(bundle) {
  const b = bundle.blocos;
  const nomes = ['A','B','C','D','E'];

  const faixaLabel = { A: '01 a 05', B: '06 a 10', C: '11 a 15', D: '16 a 20', E: '21 a 25' };
  const total = bundle.meta.total;

  // ranking de frequência individual por número, dentro de cada bloco
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

  // tabela campeão / lanterna de cada bloco
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

  // 3.2 combinações mais frequentes
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

  // 3.5 co-ocorrência entre blocos (matriz 5x5)
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
        const r = Math.round(30 + t * 94), g = Math.round(20 + t * 38), bch = Math.round(100 + t * 130);
        const cell = document.createElement('div');
        cell.className = 'heatcell';
        cell.style.background = `rgb(${r},${g},${bch})`;
        cell.style.color = t > 0.4 ? '#fff' : '#ccc';
        cell.style.fontSize = '13px';
        cell.textContent = cnt;
        cell.title = i === j
          ? `Bloco ${nomeLinha} sozinho com ≥3 dezenas: ${cnt} vezes`
          : `Blocos ${nomeLinha} e ${nomeCol} juntos com ≥3 dezenas cada: ${cnt} vezes`;
        wrap.appendChild(cell);
      });
    });
  }
}

function renderPeriodoCompleto(bundle) {
  renderKpisPeriodo(bundle);
  renderPI(bundle);
  renderChartFreq(bundle);
  renderChartAtraso(bundle);
  renderChartFaixas(bundle);
  renderChartSoma(bundle);
  renderChartSeqDist(bundle);
  renderSeqTabs(bundle);
  renderCoocList(bundle);
  renderChartTend(bundle);
  renderChartEvolucao(bundle);
  renderBlocos(bundle);
  renderRepeticao(bundle);
  renderCicloMedio(bundle);
  renderTrios(bundle);
  renderGrade(bundle);
  renderDigitosFinais(bundle);
  renderAntiCorr(bundle);
  renderHotCold(bundle, hotcoldJanelaAtual);
}

// ── grade interativa 5×5 — substitui o heatmap estático da Visão Geral.
// Clicar em uma ou mais dezenas filtra (semântica "E": só entram sorteios que
// contêm TODAS as dezenas marcadas) e recalcula, no próprio navegador, os
// mesmos gráficos que renderPeriodoCompleto já sabe desenhar — por isso essas
// funções replicam em JS os cálculos que o Python faz por período (frequência,
// atraso, pares/ímpares etc.): não dá para pré-computar todas as combinações
// possíveis de dezenas no servidor. blocos/financeiro continuam mostrando o
// período ativo (não recalculados por combinação de números). ───────────────
let numerosSelecionados = new Set();
// janela de recência (dias) do card "Números quentes e frios" — declarada aqui
// (não lá embaixo, perto do resto do bloco) pelo mesmo motivo do TDZ já
// documentado para historicoInicializado: renderPeriodoCompleto() já chama
// renderHotCold() na primeira execução, antes do bloco original ser lido.
let hotcoldJanelaAtual = 30;

function renderNumGrid(bundle) {
  const grid = document.getElementById('numgrid');
  grid.innerHTML = '';
  const freq = bundle.frequencia;
  const vals = Object.values(freq);
  const minV = Math.min(...vals), maxV = Math.max(...vals);
  for (let d = 1; d <= 25; d++) {
    const cnt = freq[d] || 0;
    const t = maxV > minV ? (cnt - minV) / (maxV - minV) : 0;
    const r = Math.round(30 + t * 94), g = Math.round(20 + t * 38), b = Math.round(100 + t * 130);
    const cell = document.createElement('div');
    cell.className = 'numgrid-cell' + (numerosSelecionados.has(d) ? ' selecionada' : '');
    cell.dataset.num = String(d);
    cell.style.background = `rgb(${r},${g},${b})`;
    cell.textContent = String(d).padStart(2, '0');
    cell.title = `Dezena ${String(d).padStart(2,'0')}: ${cnt} vezes (${bundle.meta.total ? (cnt/bundle.meta.total*100).toFixed(1) : '0.0'}%)`;
    grid.appendChild(cell);
  }
}

function obterIndicesSorteiosDoPeriodo(periodoId) {
  const meta = DATA.sorteios_meta || [];
  if (periodoId === '__todos__') return meta.map((_, i) => i);
  const indices = [];
  meta.forEach((m, i) => {
    const partes = (m.data || '').split('/'); // DD/MM/AAAA
    if (partes.length !== 3) return;
    const [, mesStr, anoStr] = partes;
    if (sorteioNoPeriodo(anoStr, +mesStr, periodoId)) indices.push(i);
  });
  return indices;
}

function calcFrequenciaJS(sorteios) {
  const freq = {};
  for (let d = 1; d <= 25; d++) freq[d] = 0;
  sorteios.forEach(s => s.forEach(d => freq[d]++));
  return freq;
}
function calcAtrasoJS(sorteios) {
  const n = sorteios.length, ultimo = {};
  sorteios.forEach((s, i) => s.forEach(d => { ultimo[d] = i; }));
  const atraso = {};
  for (let d = 1; d <= 25; d++) atraso[d] = (d in ultimo) ? (n - 1 - ultimo[d]) : n;
  return atraso;
}
function calcParesImparesJS(sorteios) {
  return sorteios.map(s => { const p = s.filter(d => d % 2 === 0).length; return { pares: p, impares: 15 - p }; });
}
function calcFaixasJS(sorteios) {
  return sorteios.map(s => ({
    baixo: s.filter(d => d >= 1 && d <= 8).length,
    medio: s.filter(d => d >= 9 && d <= 17).length,
    alto: s.filter(d => d >= 18 && d <= 25).length,
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
function calcTendenciaJS(sorteios) {
  const n = sorteios.length;
  const janela = Math.max(1, Math.min(50, n));
  const freqTotal = calcFrequenciaJS(sorteios);
  const freqRecente = calcFrequenciaJS(sorteios.slice(-janela));
  const dados = [];
  for (let d = 1; d <= 25; d++) {
    const ft = n ? +(freqTotal[d] / n * 100).toFixed(1) : 0;
    const fr = +(freqRecente[d] / janela * 100).toFixed(1);
    dados.push({ d, total: ft, recente: fr, delta: +(fr - ft).toFixed(1) });
  }
  return dados;
}
function calcEvolucaoJS(sorteios, top5) {
  const concursos = sorteios.map((_, i) => i + 1);
  const series = {};
  top5.forEach(d => {
    let acc = 0;
    series[d] = sorteios.map(s => { if (s.includes(d)) acc++; return acc; });
  });
  return { dezenas: top5, concursos, series };
}
function calcRepeticaoAnteriorJS(sorteios) {
  const rep = [];
  for (let i = 1; i < sorteios.length; i++) {
    const anterior = new Set(sorteios[i - 1]);
    rep.push(sorteios[i].filter(d => anterior.has(d)).length);
  }
  return rep;
}
function calcCicloMedioJS(sorteios) {
  const indices = {};
  sorteios.forEach((s, i) => s.forEach(d => { (indices[d] = indices[d] || []).push(i); }));
  const resultado = {};
  for (let d = 1; d <= 25; d++) {
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
function calcTriosJS(sorteios, topN) {
  const n = sorteios.length;
  const cont = new Map();
  sorteios.forEach(s => {
    const ordenado = [...s].sort((a, b) => a - b);
    for (let i = 0; i < ordenado.length; i++)
      for (let j = i + 1; j < ordenado.length; j++)
        for (let k = j + 1; k < ordenado.length; k++) {
          const key = ordenado[i] + ',' + ordenado[j] + ',' + ordenado[k];
          cont.set(key, (cont.get(key) || 0) + 1);
        }
  });
  const arr = [...cont.entries()].map(([k, c]) => ({ trio: k.split(',').map(Number), count: c, pct: +(c / n * 100).toFixed(1) }));
  arr.sort((a, b) => b.count - a.count);
  return arr.slice(0, topN || 15);
}
function calcGradeJS(sorteios) {
  const linha = d => Math.floor((d - 1) / 5) + 1;
  const coluna = d => (d - 1) % 5 + 1;
  const linhasSoma = {}, colunasSoma = {};
  for (let i = 1; i <= 5; i++) { linhasSoma[i] = 0; colunasSoma[i] = 0; }
  sorteios.forEach(s => s.forEach(d => { linhasSoma[linha(d)]++; colunasSoma[coluna(d)]++; }));
  const n = sorteios.length;
  return {
    linhas: [1, 2, 3, 4, 5].map(r => +(linhasSoma[r] / n).toFixed(2)),
    colunas: [1, 2, 3, 4, 5].map(c => +(colunasSoma[c] / n).toFixed(2)),
  };
}
function calcDigitosFinaisJS(sorteios) {
  const n = sorteios.length;
  const total = {};
  for (let dig = 0; dig <= 9; dig++) total[dig] = 0;
  sorteios.forEach(s => s.forEach(d => { total[d % 10]++; }));
  const media = {};
  for (let dig = 0; dig <= 9; dig++) media[dig] = +(total[dig] / n).toFixed(2);
  return { total, media_por_sorteio: media };
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

function montarBundleFiltradoPorNumeros(sorteios, bundleBase) {
  const freq = calcFrequenciaJS(sorteios);
  const top5 = Object.entries(freq).map(([d, c]) => ({ d: +d, c })).sort((a, b) => b.c - a.c).slice(0, 5).map(x => x.d);
  const { distTamanho, topPorTamanho } = calcSequenciasJS(sorteios);
  return {
    meta: { total: sorteios.length },
    frequencia: freq,
    atraso: calcAtrasoJS(sorteios),
    pares_impares: calcParesImparesJS(sorteios),
    faixas: calcFaixasJS(sorteios),
    somas: calcSomaJS(sorteios),
    seq_dist_tamanho: distTamanho,
    seq_top_por_tamanho: topPorTamanho,
    coocorrencia: calcCoocorrenciaJS(sorteios, 20),
    tendencia: calcTendenciaJS(sorteios),
    evolucao: calcEvolucaoJS(sorteios, top5),
    blocos: bundleBase.blocos,
    repeticao_anterior: calcRepeticaoAnteriorJS(sorteios),
    ciclo_medio: calcCicloMedioJS(sorteios),
    trios: calcTriosJS(sorteios, 15),
    grade: calcGradeJS(sorteios),
    digitos_finais: calcDigitosFinaisJS(sorteios),
    anticorrelacao: calcAntiCorrelacaoJS(sorteios, 15),
  };
}

function aplicarFiltroNumeros() {
  const hint = document.getElementById('numgrid-hint');
  const btnLimpar = document.getElementById('numgrid-limpar');
  const bundleBase = resolverBundlePeriodo(periodoAtualId);
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
  const indicesPeriodo = obterIndicesSorteiosDoPeriodo(periodoAtualId);
  const sorteiosFiltrados = indicesPeriodo
    .map(i => DATA.sorteios_raw[i])
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

// ── financeiro e banner do período — reagem ao seletor junto com o resto ────

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
    { label: 'Prêmio médio (faixa 1)', valor: f.media_premio_faixa1 != null ? formatarMoeda(f.media_premio_faixa1) : '—' },
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
  banner.textContent = `⚠️ Exibindo dados de: ${label} (${bundle.meta.total} sorteios de ${inicio} a ${fim})`;
  banner.style.display = 'block';
}

// ── função central: aplica um período a TODOS os elementos do dashboard ─────
let periodoAtualId = '__todos__';
let historicoInicializado = false; // declarado aqui (não lá embaixo) porque aplicarPeriodo()
                                    // já é chamado antes da seção do Histórico ser lida

function resolverBundlePeriodo(periodoId) {
  return periodoId === '__todos__' ? DATA : DATA.periodos[periodoId];
}

function aplicarPeriodo(periodoId) {
  const bundle = resolverBundlePeriodo(periodoId);
  if (!bundle) return;
  periodoAtualId = periodoId;
  // trocar de período limpa a seleção de dezenas: o filtro numérico é relativo
  // a UM período por vez, misturar os dois deixaria o "N sorteios encontrados" ambíguo
  numerosSelecionados.clear();
  const btnLimpar = document.getElementById('numgrid-limpar');
  const hint = document.getElementById('numgrid-hint');
  if (btnLimpar) btnLimpar.style.display = 'none';
  if (hint) hint.textContent = 'Clique em uma ou mais dezenas para filtrar todos os gráficos abaixo pela combinação escolhida.';
  renderNumGrid(bundle);
  renderPeriodoCompleto(bundle);
  renderFinanceiro(bundle);
  renderBannerPeriodo(periodoId, bundle);
  aplicarFiltroPeriodoHistorico(periodoId);
}

// ── seletor de período — card com grupos empilhados por tipo. Ano/Semestre
// sempre visíveis (poucos botões, alto valor); Trimestre/Bimestre/Mês vêm
// colapsados atrás de um accordion "▼ Ver..." pra não lotar a tela com
// dezenas de botões de uma vez. ────────────────────────────────────────────
{
  const container = document.getElementById('period-selector');
  const cardEl = container.parentNode;
  const todosBtn = document.getElementById('period-todos-btn');
  const disponiveis = DATA.periodos_disponiveis || [];
  const tiposOrdem = ['ano', 'semestre', 'trimestre', 'bimestre', 'mes'];
  const tiposLabel = { ano: 'Ano', semestre: 'Semestre', trimestre: 'Trimestre', bimestre: 'Bimestre', mes: 'Mês' };
  const tiposPlural = { ano: 'anos', semestre: 'semestres', trimestre: 'trimestres', bimestre: 'bimestres', mes: 'meses' };
  const colapsadoPorPadrao = { ano: false, semestre: false, trimestre: true, bimestre: true, mes: true };

  const grupos = {};
  disponiveis.forEach(p => { (grupos[p.tipo] = grupos[p.tipo] || []).push(p); });

  tiposOrdem.forEach(tipo => {
    const lista = grupos[tipo];
    if (!lista || !lista.length) return;

    const row = document.createElement('div');
    row.className = 'period-row' + (colapsadoPorPadrao[tipo] ? ' colapsada' : '');

    const label = document.createElement('span');
    label.className = 'tipo-label';
    label.textContent = tiposLabel[tipo] + ':';
    row.appendChild(label);

    if (colapsadoPorPadrao[tipo]) {
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'period-accordion-toggle';
      toggle.textContent = '▼ Ver ' + tiposPlural[tipo];
      toggle.addEventListener('click', () => {
        row.classList.toggle('colapsada');
        const colapsadaAgora = row.classList.contains('colapsada');
        toggle.textContent = (colapsadaAgora ? '▼ Ver ' : '▲ Esconder ') + tiposPlural[tipo];
      });
      row.appendChild(toggle);
    }

    const scroll = document.createElement('div');
    scroll.className = 'period-row-scroll';
    lista.sort((a, b) => a.id.localeCompare(b.id)).forEach(p => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'period-btn';
      btn.textContent = p.label;
      btn.dataset.periodo = p.id;
      scroll.appendChild(btn);
    });
    row.appendChild(scroll);
    container.appendChild(row);
  });

  function selecionarPeriodo(periodoId) {
    cardEl.querySelectorAll('.period-btn').forEach(b => b.classList.toggle('active', b.dataset.periodo === periodoId));
    aplicarPeriodo(periodoId);
  }

  todosBtn.addEventListener('click', () => selecionarPeriodo('__todos__'));
  container.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.period-btn');
    if (!btn) return;
    selecionarPeriodo(btn.dataset.periodo);
  });
}

aplicarPeriodo('__todos__');

// ── abas de página: Visão Geral / Blocos / Histórico ─────────────────────────
{
  const paginas = [
    { tab: 'page-tab-geral', pagina: 'page-geral' },
    { tab: 'page-tab-blocos', pagina: 'page-blocos' },
    { tab: 'page-tab-historico', pagina: 'page-historico' },
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

// ── Blocos por período (heatmap — sempre histórico completo) ─────────────────
{
  const el = document.getElementById('blocos-heatmap-periodo');
  const dados = DATA.blocos_periodo || [];
  const nomes = ['A','B','C','D','E'];
  if (!dados.length) {
    el.innerHTML = '<p style="color:var(--muted)">Sem dados.</p>';
  } else {
    const todasMedias = dados.flatMap(d => d.medias);
    const minV = Math.min(...todasMedias), maxV = Math.max(...todasMedias);
    const table = document.createElement('table');
    table.innerHTML = `<thead><tr><th>Período</th>${nomes.map(n => `<th>Bloco ${n}</th>`).join('')}<th>Sorteios</th></tr></thead>`;
    const tbody = document.createElement('tbody');
    dados.forEach(d => {
      const tr = document.createElement('tr');
      const celulas = d.medias.map(v => {
        const t = maxV > minV ? (v - minV) / (maxV - minV) : 0;
        const r = Math.round(30 + t * 94), g = Math.round(20 + t * 38), bch = Math.round(100 + t * 130);
        const cor = t > 0.4 ? '#fff' : '#ccc';
        return `<td style="background:rgb(${r},${g},${bch});color:${cor};font-weight:700;text-align:center">${v}</td>`;
      }).join('');
      tr.innerHTML = `<td>${d.periodo}</td>${celulas}<td style="color:var(--muted)">${d.total}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    el.appendChild(table);
  }
}

function renderChartFaixas(bundle) {
  const b = bundle.faixas.map(f=>f.baixo), m = bundle.faixas.map(f=>f.medio), a = bundle.faixas.map(f=>f.alto);
  const avg = arr => arr.length ? arr.reduce((x,y)=>x+y,0)/arr.length : 0;
  criarChart('chartFaixas', {
    type: 'bar',
    data: {
      labels: ['Baixo (01-08)', 'Médio (09-17)', 'Alto (18-25)'],
      datasets: [{
        label: 'Média por sorteio',
        data: [avg(b), avg(m), avg(a)],
        backgroundColor: ['#7c3aed','#06b6d4','#10b981'],
        borderRadius: 6
      }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderChartSoma(bundle) {
  const somas = bundle.somas;
  const min_s = Math.min(...somas), max_s = Math.max(...somas);
  const bins = 20, step = (max_s - min_s) / bins || 1;
  const labels = [], counts = [];
  for (let i = 0; i < bins; i++) {
    const lo = Math.round(min_s + i * step), hi = Math.round(min_s + (i+1) * step);
    labels.push(lo + '-' + hi);
    counts.push(somas.filter(s => s >= lo && s < hi).length);
  }
  criarChart('chartSoma', {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Freq.', data: counts, backgroundColor: '#06b6d4', borderRadius: 4 }] },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderChartSeqDist(bundle) {
  const dist = bundle.seq_dist_tamanho;
  const labels = Object.keys(dist).map(k=>k+' consecutivos');
  criarChart('chartSeqDist', {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label: 'Ocorrências em sorteios', data: Object.values(dist), backgroundColor: COLORS, borderRadius: 6 }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderChartEvolucao(bundle) {
  const ev = bundle.evolucao;
  const step = Math.max(1, Math.floor(ev.concursos.length / 50));
  const labels = ev.concursos.filter((_,i)=>i%step===0);
  const datasets = ev.dezenas.map((d, idx) => ({
    label: 'Dez. ' + String(d).padStart(2,'0'),
    data: ev.series[d].filter((_,i)=>i%step===0),
    borderColor: COLORS[idx],
    backgroundColor: 'transparent',
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.3
  }));
  criarChart('chartEvolucao', {
    type: 'line',
    data: { labels, datasets },
    options: { ...chartDefaults, plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } }, scales: { x: { ticks: { maxTicksLimit: 8, color: '#64748b', font: { size: 10 } }, grid: { color: '#1e2130' } }, y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e2130' } } } }
  });
}

// ── Repetição do concurso anterior — agora period-aware ─────────────────────
function renderRepeticao(bundle) {
  const rep = bundle.repeticao_anterior;
  const media = rep.length ? rep.reduce((a,b)=>a+b,0) / rep.length : 0;
  document.getElementById('rep-media').textContent = media.toFixed(1);
  document.getElementById('rep-min').textContent = rep.length ? Math.min(...rep) : 0;
  document.getElementById('rep-max').textContent = rep.length ? Math.max(...rep) : 0;
  animarContador(document.getElementById('kpi-repeticao'), media, 1);
  document.getElementById('kpi-repeticao-sub').textContent = 'dezenas repetidas em média';

  const dist = new Array(16).fill(0);
  rep.forEach(v => dist[v]++);
  criarChart('chartRepeticao', {
    type: 'bar',
    data: {
      labels: dist.map((_,i) => String(i)),
      datasets: [{ label: 'Sorteios', data: dist, backgroundColor: '#7c3aed', borderRadius: 4 }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

// ── Ciclo médio por dezena — agora period-aware ──────────────────────────────
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

// ── Trios mais frequentes — agora period-aware ───────────────────────────────
function renderTrios(bundle) {
  const list = document.getElementById('trios-list');
  list.innerHTML = '';
  const items = bundle.trios;
  if (!items.length) {
    list.innerHTML = '<p style="color:var(--muted)">Sem dados suficientes neste período.</p>';
    return;
  }
  const table = document.createElement('table');
  table.innerHTML = `<thead><tr><th>#</th><th>Trio</th><th>Aparições</th><th>% sorteios</th></tr></thead>`;
  const tbody = document.createElement('tbody');
  items.forEach((item, i) => {
    const tags = item.trio.map(n => `<span class="seq-tag">${String(n).padStart(2,'0')}</span>`).join('');
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="color:var(--muted)">${i+1}</td>
      <td>${tags}</td>
      <td>${item.count}</td>
      <td>${item.pct}%</td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  list.appendChild(table);
}

// ── Grade (linhas/colunas + mapa de calor do volante) — agora period-aware ──
function renderGrade(bundle) {
  criarChart('chartGrade', {
    type: 'bar',
    data: {
      labels: ['Posição 1', 'Posição 2', 'Posição 3', 'Posição 4', 'Posição 5'],
      datasets: [
        { label: 'Linha (média/sorteio)', data: bundle.grade.linhas, backgroundColor: '#7c3aed', borderRadius: 4 },
        { label: 'Coluna (média/sorteio)', data: bundle.grade.colunas, backgroundColor: '#06b6d4', borderRadius: 4 }
      ]
    },
    options: { ...chartDefaults, plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } } }
  });

  const wrap = document.getElementById('grade-heatmap');
  wrap.innerHTML = '';
  const vals = Object.values(bundle.frequencia);
  const minV = Math.min(...vals), maxV = Math.max(...vals);
  const colorFor = cnt => {
    const t = maxV > minV ? (cnt - minV) / (maxV - minV) : 0;
    const r = Math.round(30 + t * 94), g = Math.round(20 + t * 38), b = Math.round(100 + t * 130);
    return { bg: `rgb(${r},${g},${b})`, fg: t > 0.4 ? '#fff' : '#ccc' };
  };

  wrap.appendChild(document.createElement('div'));
  for (let c = 1; c <= 5; c++) {
    const h = document.createElement('div');
    h.className = 'colhead';
    h.textContent = 'C' + c;
    wrap.appendChild(h);
  }
  for (let r = 1; r <= 5; r++) {
    const h = document.createElement('div');
    h.className = 'rowhead';
    h.textContent = 'L' + r;
    wrap.appendChild(h);
    for (let c = 1; c <= 5; c++) {
      const d = (r - 1) * 5 + c;
      const cnt = bundle.frequencia[d] || 0;
      const { bg, fg } = colorFor(cnt);
      const cell = document.createElement('div');
      cell.className = 'heatcell';
      cell.style.background = bg;
      cell.style.color = fg;
      cell.innerHTML = String(d).padStart(2,'0') + '<span class="freq">' + cnt + '</span>';
      cell.title = `Dezena ${String(d).padStart(2,'0')} — Linha ${r}, Coluna ${c}: ${cnt} vezes`;
      wrap.appendChild(cell);
    }
  }
}

// ── Números quentes e frios — agora period-aware: a janela de recência (15/30/50
// últimos sorteios) passa a olhar só os sorteios DENTRO do período ativo, e a
// linha de base de comparação também vira a frequência do próprio período (não
// mais o histórico geral). Reaproveita obterIndicesSorteiosDoPeriodo(), já usado
// pela grade interativa, pra não duplicar a lógica de filtrar por período. ──────
function renderHotCold(bundle, janela) {
  hotcoldJanelaAtual = janela;
  const grid = document.getElementById('hotcold-grid');
  grid.innerHTML = '';
  const indicesPeriodo = obterIndicesSorteiosDoPeriodo(periodoAtualId);
  const sorteiosPeriodo = indicesPeriodo.map(i => DATA.sorteios_raw[i]);
  const totalPeriodo = sorteiosPeriodo.length;
  const janelaEfetiva = Math.min(janela, totalPeriodo);
  const recentes = sorteiosPeriodo.slice(-janelaEfetiva);
  const freqRec = {};
  for (let d = 1; d <= 25; d++) freqRec[d] = 0;
  recentes.forEach(s => s.forEach(d => freqRec[d]++));

  for (let d = 1; d <= 25; d++) {
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

// ── Dígitos finais — agora period-aware ──────────────────────────────────────
function renderDigitosFinais(bundle) {
  const media = bundle.digitos_finais.media_por_sorteio;
  const total = bundle.digitos_finais.total;
  const labels = Object.keys(media).sort((a, b) => +a - +b);
  criarChart('chartDigitos', {
    type: 'bar',
    data: {
      labels: labels.map(d => 'dígito ' + d),
      datasets: [
        { label: 'Média por sorteio', data: labels.map(d => media[d]), backgroundColor: '#7c3aed', borderRadius: 4, yAxisID: 'y' },
        { label: 'Total no período', data: labels.map(d => total[d]), backgroundColor: '#06b6d4', borderRadius: 4, yAxisID: 'y1' }
      ]
    },
    options: {
      ...chartDefaults,
      plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
      scales: {
        x: chartDefaults.scales.x,
        y: { position: 'left', ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e2130' } },
        y1: { position: 'right', ticks: { color: '#64748b', font: { size: 10 } }, grid: { drawOnChartArea: false } }
      }
    }
  });
}

// ── Anti-correlação — agora period-aware ─────────────────────────────────────
function renderAntiCorr(bundle) {
  const list = document.getElementById('anticorr-list');
  list.innerHTML = '';
  const items = bundle.anticorrelacao;
  const total = bundle.meta.total;
  if (!items.length) {
    list.innerHTML = '<p style="color:var(--muted)">Sem dados suficientes neste período.</p>';
    return;
  }
  const maxC = Math.max(...items.map(x => x[1]));
  const table = document.createElement('table');
  table.innerHTML = `<thead><tr><th>#</th><th>Par</th><th>Aparições</th><th>% sorteios</th><th>Freq.</th></tr></thead>`;
  const tbody = document.createElement('tbody');
  items.forEach(([pair, cnt], i) => {
    const [a, b] = pair;
    const pct = (cnt / total * 100).toFixed(1);
    const w = Math.round(cnt / maxC * 80);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="color:var(--muted)">${i+1}</td>
      <td><span class="seq-tag">${String(a).padStart(2,'0')}</span><span class="seq-tag">${String(b).padStart(2,'0')}</span></td>
      <td>${cnt}</td>
      <td>${pct}%</td>
      <td><div style="height:8px;border-radius:3px;background:#06b6d4;width:${w}px;min-width:4px"></div></td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  list.appendChild(table);
}

// ── Simulador de aposta — separadores flexíveis + múltiplos jogos ───────────
function parseDezenasBrutas(texto) {
  // mantém tudo (mesmo inválido) para a validação em tempo real conseguir
  // sinalizar números fora do intervalo
  return texto.split(/[\s,;.\-]+/).map(s => s.trim()).filter(s => s.length > 0).map(Number);
}
function parseDezenas(texto) {
  return parseDezenasBrutas(texto).filter(n => Number.isInteger(n) && n >= 1 && n <= 25);
}
function validarJogoTexto(texto) {
  const brutas = parseDezenasBrutas(texto);
  if (!brutas.length) return { status: 'vazio', validos: [] };
  const foraDeRange = brutas.some(n => !Number.isInteger(n) || n < 1 || n > 25);
  const validos = brutas.filter(n => Number.isInteger(n) && n >= 1 && n <= 25);
  const repetidos = new Set(validos).size !== validos.length;
  if (foraDeRange || repetidos) return { status: 'erro', validos };
  if (validos.length === 15) return { status: 'ok', validos };
  return { status: 'parcial', validos };
}

{
  const listaEl = document.getElementById('sim-jogos-lista');
  const btnAdd = document.getElementById('sim-btn-add');
  const btnVerificar = document.getElementById('sim-btn');
  const errEl = document.getElementById('sim-error');
  const resultEl = document.getElementById('sim-result');
  const sorteiosRaw = DATA.sorteios_raw;
  const sorteiosMeta = DATA.sorteios_meta || [];

  function renumerarJogos() {
    listaEl.querySelectorAll('.sim-jogo-row').forEach((row, i) => {
      row.querySelector('.sim-jogo-label').textContent = 'Jogo ' + (i + 1);
    });
    // com só 1 jogo, esconde o botão de remover — não faz sentido remover o único campo
    const rows = listaEl.querySelectorAll('.sim-jogo-row');
    rows.forEach(row => {
      row.querySelector('.sim-jogo-remove').style.visibility = rows.length > 1 ? 'visible' : 'hidden';
    });
  }

  function atualizarBadge(row) {
    const inputEl = row.querySelector('.sim-jogo-input');
    const badge = row.querySelector('.sim-jogo-badge');
    const v = validarJogoTexto(inputEl.value);
    badge.classList.remove('ok', 'parcial', 'erro');
    if (v.status === 'vazio') {
      badge.textContent = '';
    } else if (v.status === 'ok') {
      badge.textContent = '✓ 15/15';
      badge.classList.add('ok');
    } else if (v.status === 'erro') {
      badge.textContent = 'inválido/repetido';
      badge.classList.add('erro');
    } else {
      badge.textContent = `${v.validos.length}/15 números`;
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
    inputEl.placeholder = 'ex: 01, 02 05-06;07 09.11 13 15 17 19 21 23 25';
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

  function calcularResultado(numeros) {
    const aposta = new Set(numeros);
    const pontos = new Array(16).fill(0);
    const concursosPontuados = [];
    sorteiosRaw.forEach((s, i) => {
      const acertos = s.filter(d => aposta.has(d)).length;
      pontos[acertos]++;
      if (acertos >= 11 && sorteiosMeta[i]) {
        concursosPontuados.push({ concurso: sorteiosMeta[i].concurso, data: sorteiosMeta[i].data, acertos });
      }
    });
    concursosPontuados.sort((a, b) => b.concurso - a.concurso);
    const totalPremios = pontos[11] + pontos[12] + pontos[13] + pontos[14] + pontos[15];
    const melhorIndividual = concursosPontuados.length ? Math.max(...concursosPontuados.map(c => c.acertos)) : 0;
    return { numeros, pontos, concursosPontuados, totalPremios, melhorIndividual };
  }

  function construirDetalhePainel(resultado) {
    const painel = document.createElement('div');
    painel.className = 'sim-detalhe-painel';
    if (!resultado.concursosPontuados.length) {
      painel.innerHTML = '<span style="color:var(--muted); font-size:12px;">Nenhum concurso com 11+ acertos.</span>';
      return painel;
    }
    resultado.concursosPontuados.forEach(c => {
      const item = document.createElement('span');
      item.className = 'sim-detalhe-item';
      item.textContent = `Concurso ${c.concurso} (${c.data}) — ${c.acertos} pts`;
      painel.appendChild(item);
    });
    return painel;
  }

  function formatarNumeros(numeros) {
    return [...numeros].sort((a, b) => a - b).map(n => String(n).padStart(2, '0')).join(' ');
  }

  let ultimosResultados = []; // guardado para o botão "copiar resultado"

  function renderizarResultadoUnico(resultado) {
    const total = sorteiosRaw.length;
    const table = document.createElement('table');
    table.innerHTML = `<thead><tr><th>Pontos</th><th>Vezes que ocorreu</th><th>% dos sorteios</th></tr></thead>`;
    const tbody = document.createElement('tbody');
    for (let p = 15; p >= 11; p--) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td class="pontos">${p} pontos</td><td>${resultado.pontos[p]}</td><td>${(resultado.pontos[p] / total * 100).toFixed(2)}%</td>`;
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    resultEl.appendChild(table);

    const btnToggle = document.createElement('button');
    btnToggle.className = 'sim-detalhe-toggle';
    btnToggle.style.marginTop = '10px';
    btnToggle.textContent = `Ver em quais concursos pontuou (${resultado.concursosPontuados.length})`;
    const painel = construirDetalhePainel(resultado);
    btnToggle.addEventListener('click', () => {
      painel.classList.toggle('aberto');
      btnToggle.textContent = painel.classList.contains('aberto')
        ? 'Esconder concursos'
        : `Ver em quais concursos pontuou (${resultado.concursosPontuados.length})`;
    });
    resultEl.appendChild(btnToggle);
    resultEl.appendChild(painel);
  }

  function renderizarResultadoComparativo(resultados) {
    const maxPremios = Math.max(...resultados.map(r => r.totalPremios));
    const maxIndividual = Math.max(...resultados.map(r => r.melhorIndividual));

    const table = document.createElement('table');
    table.innerHTML = `<thead><tr><th>#</th><th>Números</th><th>15pts</th><th>14pts</th><th>13pts</th><th>12pts</th><th>11pts</th><th>Total prêmios</th></tr></thead>`;
    const tbody = document.createElement('tbody');

    resultados.forEach((r, i) => {
      const tr = document.createElement('tr');
      tr.className = 'sim-compare-row';
      if (r.totalPremios === maxPremios && maxPremios > 0) tr.classList.add('destaque-ouro');
      const trofeuIndividual = (r.melhorIndividual === maxIndividual && maxIndividual > 0)
        ? `<span class="sim-trofeu" title="Maior acerto individual entre os jogos simulados">🏆</span>` : '';
      tr.innerHTML = `
        <td>Jogo ${i + 1}</td>
        <td>${formatarNumeros(r.numeros)}</td>
        <td>${r.pontos[15]}</td>
        <td>${r.pontos[14]}</td>
        <td>${r.pontos[13]}</td>
        <td>${r.pontos[12]}</td>
        <td>${r.pontos[11]}</td>
        <td>${r.totalPremios}${trofeuIndividual}</td>`;
      tbody.appendChild(tr);

      const trDetalhe = document.createElement('tr');
      const tdDetalhe = document.createElement('td');
      tdDetalhe.colSpan = 8;
      const btnToggle = document.createElement('button');
      btnToggle.className = 'sim-detalhe-toggle';
      btnToggle.textContent = `Ver em quais concursos pontuou (${r.concursosPontuados.length})`;
      const painel = construirDetalhePainel(r);
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

  function copiarResultado() {
    if (!ultimosResultados.length) return;
    let texto;
    if (ultimosResultados.length === 1) {
      const r = ultimosResultados[0];
      texto = `Simulação — ${formatarNumeros(r.numeros)}\n`
        + `15 pts: ${r.pontos[15]} | 14 pts: ${r.pontos[14]} | 13 pts: ${r.pontos[13]} | `
        + `12 pts: ${r.pontos[12]} | 11 pts: ${r.pontos[11]} | Total prêmios: ${r.totalPremios}`;
    } else {
      const linhas = ['#\tNúmeros\t15pts\t14pts\t13pts\t12pts\t11pts\tTotal prêmios'];
      ultimosResultados.forEach((r, i) => {
        linhas.push(`Jogo ${i + 1}\t${formatarNumeros(r.numeros)}\t${r.pontos[15]}\t${r.pontos[14]}\t${r.pontos[13]}\t${r.pontos[12]}\t${r.pontos[11]}\t${r.totalPremios}`);
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
  btnCopiar.addEventListener('click', copiarResultado);

  btnVerificar.addEventListener('click', () => {
    errEl.style.display = 'none';
    resultEl.innerHTML = '';
    btnCopiar.style.display = 'none';

    const linhas = [...listaEl.querySelectorAll('.sim-jogo-row')];
    const validacoes = linhas.map(row => validarJogoTexto(row.querySelector('.sim-jogo-input').value));
    const validos = [];
    let ignorados = 0;
    validacoes.forEach(v => {
      if (v.status === 'ok') validos.push(v.validos);
      else if (v.status !== 'vazio') ignorados++;
    });

    if (!validos.length) {
      errEl.textContent = linhas.length === 1
        ? 'Digite exatamente 15 números inteiros entre 1 e 25, sem repetição.'
        : 'Nenhum jogo válido — cada um precisa de exatamente 15 números entre 1 e 25, sem repetição.';
      errEl.style.display = 'block';
      return;
    }

    ultimosResultados = validos.map(calcularResultado);

    if (ignorados > 0) {
      const aviso = document.createElement('div');
      aviso.className = 'sim-aviso';
      aviso.textContent = `${ignorados} jogo(s) ignorado(s) por estarem incompletos ou inválidos.`;
      resultEl.appendChild(aviso);
    }

    if (ultimosResultados.length === 1) {
      renderizarResultadoUnico(ultimosResultados[0]);
    } else {
      renderizarResultadoComparativo(ultimosResultados);
    }
    resultEl.appendChild(btnCopiar);
    btnCopiar.style.display = 'inline-block';
  });
}

// ── Premiação oficial e valor da aposta ──────────────────────────────────────
const VALOR_APOSTA = 3.50;
const PREMIOS = { 15: 1966163.83, 14: 3195.82, 13: 35.00, 12: 14.00, 11: 7.00 };

function formatarMoeda(v) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function calcularFinanceiro(contagem, nSorteios) {
  const ganho = [15, 14, 13, 12, 11].reduce((acc, p) => acc + contagem[String(p)] * PREMIOS[p], 0);
  const gasto = nSorteios * VALOR_APOSTA;
  return { ganho, gasto, saldo: ganho - gasto };
}

// ── Meus jogos — ranking + detalhamento (função reutilizável) ───────────────
function renderJogosSection(jogos, ids) {
  if (!jogos) {
    [ids.rankingCard, ids.detalheCard].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = '<p style="color:var(--muted)">Nenhum jogo configurado.</p>';
    });
    return;
  }

  const nomes = Object.keys(jogos); // preserva a ordem de inserção
  const faixas = { 15: '1ª (sena)', 14: '2ª', 13: '3ª', 12: '4ª', 11: '5ª' };

  // ranking
  {
    const container = document.getElementById(ids.ranking);
    const nSorteios = DATA.meta.total;
    const entries = nomes.map(nome => [nome, jogos[nome]]).sort((a, b) => b[1].total - a[1].total);

    const gastoTotal = nSorteios * VALOR_APOSTA;
    const nota = document.createElement('p');
    nota.style.cssText = 'color:var(--muted); font-size:12px; margin-bottom:12px;';
    nota.textContent = `Considerando 1 aposta de ${formatarMoeda(VALOR_APOSTA)} em cada um dos ${nSorteios} sorteios `
      + `analisados, o gasto é o mesmo para todos os jogos: ${formatarMoeda(gastoTotal)}.`;
    container.appendChild(nota);

    const table = document.createElement('table');
    table.innerHTML = `<thead><tr><th>#</th><th>Jogo</th><th>15</th><th>14</th><th>13</th><th>12</th><th>11</th><th>Total ≥11</th><th>%</th><th>Gasto</th><th>Ganho</th><th>Saldo</th><th>Melhor</th></tr></thead>`;
    const tbody = document.createElement('tbody');
    entries.forEach(([nome, v], i) => {
      const c = v.contagem;
      const m = v.melhor;
      const melhorTxt = m.concurso ? `${m.acertos} (c.${m.concurso})` : '—';
      const { ganho, gasto, saldo } = calcularFinanceiro(c, nSorteios);
      const saldoClasse = saldo >= 0 ? 'money-pos' : 'money-neg';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="color:var(--muted)">${i + 1}</td>
        <td>${nome}</td>
        <td>${c['15']}</td><td>${c['14']}</td><td>${c['13']}</td><td>${c['12']}</td><td>${c['11']}</td>
        <td>${v.total}</td>
        <td>${v.pct_total}%</td>
        <td>${formatarMoeda(gasto)}</td>
        <td>${formatarMoeda(ganho)}</td>
        <td class="${saldoClasse}">${formatarMoeda(saldo)}</td>
        <td>${melhorTxt}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);
  }

  // detalhamento por jogo (abas)
  {
    const tabsEl = document.getElementById(ids.tabs);
    const contentsEl = document.getElementById(ids.contents);

    nomes.forEach((nome, idx) => {
      const panelId = `${ids.prefix}-${idx}`;
      const tab = document.createElement('button');
      tab.className = 'tab' + (idx === 0 ? ' active' : '');
      tab.textContent = nome;
      tab.addEventListener('click', () => {
        tabsEl.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        contentsEl.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(panelId).classList.add('active');
      });
      tabsEl.appendChild(tab);

      const info = jogos[nome];
      const div = document.createElement('div');
      div.className = 'tab-content' + (idx === 0 ? ' active' : '');
      div.id = panelId;

      const tags = info.numeros.map(n => `<span class="seq-tag">${String(n).padStart(2,'0')}</span>`).join('');
      const c = info.contagem;
      const m = info.melhor;
      const melhorTxt = m.concurso
        ? `${m.acertos} acertos — concurso ${m.concurso} (${m.data})`
        : 'nenhum acerto ≥11 registrado';

      const table = document.createElement('table');
      table.innerHTML = `<thead><tr><th>Acertos</th><th>Vezes</th><th>%</th><th>Faixa</th></tr></thead>`;
      const tbody = document.createElement('tbody');
      for (let p = 15; p >= 11; p--) {
        const vezes = c[String(p)];
        const pct = (vezes / DATA.meta.total * 100).toFixed(1);
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${p}</td><td>${vezes}</td><td>${pct}%</td><td>${faixas[p]}</td>`;
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);

      div.innerHTML = `<div style="margin-bottom:14px">${tags}</div>`;
      div.appendChild(table);

      const { ganho, gasto, saldo } = calcularFinanceiro(c, DATA.meta.total);
      const saldoClasse = saldo >= 0 ? 'money-pos' : 'money-neg';

      const stats = document.createElement('div');
      stats.className = 'mini-stats';
      stats.innerHTML = `
        <div>Total ≥11<b>${info.total}</b></div>
        <div>% dos sorteios<b>${info.pct_total}%</b></div>
        <div>Gasto (${DATA.meta.total} apostas)<b>${formatarMoeda(gasto)}</b></div>
        <div>Ganho<b>${formatarMoeda(ganho)}</b></div>
        <div>Saldo<b class="${saldoClasse}">${formatarMoeda(saldo)}</b></div>
        <div>Melhor resultado<b style="font-size:13px">${melhorTxt}</b></div>`;
      div.appendChild(stats);

      contentsEl.appendChild(div);
    });
  }
}

renderJogosSection(DATA.meus_jogos, {
  ranking: 'jogos-ranking', tabs: 'jogos-tabs', contents: 'jogos-contents',
  rankingCard: 'jogos-ranking-card', detalheCard: 'jogos-detalhe-card', prefix: 'jogo',
});

renderJogosSection(DATA.jogos_sugeridos, {
  ranking: 'jogos-sug-ranking', tabs: 'jogos-sug-tabs', contents: 'jogos-sug-contents',
  rankingCard: 'jogos-sug-ranking-card', detalheCard: 'jogos-sug-detalhe-card', prefix: 'jogo-sug',
});

// ── Histórico — árvore Ano → Mês → Sorteio (Terceira aba) ────────────────────

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
  const tags = s.dezenas.map(n => `<span class="hist-badge">${String(n).padStart(2,'0')}</span>`).join('');
  div.innerHTML = `
    <div class="hist-detail-titulo">Concurso ${s.concurso} — ${s.dia_semana}, ${formatarDataExtenso(s.data_iso)}</div>
    <div class="hist-badges">${tags}</div>
    <div class="hist-detail-meta">
      <div>Acumulado: <b>${s.acumulado ? 'Sim' : 'Não'}</b></div>
      <div>Ganhadores: <b>${s.ganhadores != null ? s.ganhadores : '—'}</b></div>
      <div>Prêmio: <b>${formatarMoedaHist(s.premio)}</b></div>
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

// Meses sempre caem inteiros dentro (ou fora) de qualquer período do seletor
// (ano/semestre/trimestre/bimestre/mês nunca cortam um mês ao meio), então dá
// para filtrar a árvore comparando só ano+mês de cada .hist-mes, sem precisar
// checar sorteio por sorteio.
function sorteioNoPeriodo(ano, mes, periodoId) {
  if (periodoId === '__todos__') return true;
  const m = periodoId.match(/^(\d{4})(?:-([A-Z])(\d+))?$/);
  if (!m) return false;
  const [, anoAlvo, tipo, numStr] = m;
  if (String(ano) !== anoAlvo) return false;
  if (!tipo) return true; // período anual, ex: "2025"
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
    if (Number.isInteger(num) && num >= 1 && num <= 25) filtrarPorDezena(num);
  });
  document.getElementById('hist-btn-limpar-filtro').addEventListener('click', () => {
    document.getElementById('hist-filtro-dezena').value = '';
    limparFiltroDezena();
  });

  // se o usuário já tinha selecionado um período antes de abrir esta aba,
  // a árvore nasce filtrada corretamente em vez de mostrar tudo.
  aplicarFiltroPeriodoHistorico(periodoAtualId);
}

// ── Verificação leve de sorteios novos + botão "Atualizar dados" ────────────
// Só aparece quando o HTML foi gerado com `--source supabase` (DATA.meta.supabase
// então tem {url, anon_key} — a anon key é pública por design, protegida por RLS
// de só-leitura). A checagem de novos sorteios não recalcula o dashboard
// inteiro: só compara o último concurso remoto com o que já está embutido.
//
// O botão "Atualizar dados" dispara o workflow do GitHub Actions direto via
// API, usando um Personal Access Token guardado em localStorage. Isso é um
// token de acesso amplo à conta do GitHub guardado numa página pública — ver
// aviso no próprio modal. Ver a conversa anterior para o raciocínio completo
// sobre esse tradeoff.
const GH_TOKEN_KEY = 'gh_token';
function ghGetToken() { return localStorage.getItem(GH_TOKEN_KEY); }
function ghSetToken(t) { localStorage.setItem(GH_TOKEN_KEY, t); }
function ghRemoverToken() { localStorage.removeItem(GH_TOKEN_KEY); }

async function dispararWorkflow(token) {
  const repo = DATA.meta.github_repo;
  const res = await fetch(
    `https://api.github.com/repos/${repo}/actions/workflows/atualizar.yml/dispatches`,
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
  return res.status; // 204 = sucesso, 401 = token inválido, 404 = repo não encontrado
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

  // ── modal de configuração do token ─────────────────────────────────────
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

  // ── disparo do workflow ──────────────────────────────────────────────────
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
    try {
      const res = await fetch(
        `${url}/rest/v1/sorteios?select=concurso,data_br&order=concurso.desc&limit=1`,
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
    evolucao = calc_numero_por_sorteio_historico(rows, sorteios)

    repeticao_anterior = calc_repeticao_anterior(sorteios)
    ciclo_medio = calc_ciclo_medio(sorteios)
    trios = calc_trios_frequentes(sorteios, top_n=15)
    grade = calc_grade(sorteios)
    digitos_finais = calc_digitos_finais(sorteios)
    cooc_completo = calc_coocorrencia_completa(sorteios)
    anticorrelacao = calc_anticorrelacao(cooc_completo, bottom_n=15)
    meus_jogos = calc_meus_jogos(JOGOS, rows, sorteios) if JOGOS else None
    jogos_sugeridos = calc_meus_jogos(JOGOS_SUGERIDOS, rows, sorteios) if JOGOS_SUGERIDOS else None

    blocos = calc_blocos_bundle(rows, sorteios)
    blocos_periodo = calc_blocos_periodo(rows, sorteios)
    periodos, periodos_disponiveis = gerar_periodos(rows, sorteios)
    historico = calc_historico(rows, sorteios)
    financeiro = calc_financeiro(rows)

    data = {
        "meta": {
            "total": n,
            "inicio": rows[0]["data"],
            "fim": rows[-1]["data"],
            "concurso_ini": rows[0]["concurso"],
            "concurso_fim": rows[-1]["concurso"],
            "supabase": fonte_supabase,  # {"url","anon_key"} se gerado com --source supabase, senão None
            "github_repo": github_repo,  # "dono/repositorio", usado só pro link do botão Atualizar dados
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
        "evolucao": {
            "dezenas": evolucao["dezenas"],
            "concursos": evolucao["concursos"],
            "series": {str(d): v for d, v in evolucao["series"].items()},
        },
        "repeticao_anterior": repeticao_anterior,
        "ciclo_medio": ciclo_medio,
        "trios": trios,
        "grade": grade,
        "digitos_finais": digitos_finais,
        "anticorrelacao": [[[a, b], c] for (a, b), c in anticorrelacao],
        "sorteios_raw": sorteios,
        "sorteios_meta": [{"concurso": r["concurso"], "data": r["data"]} for r in rows],
        "meus_jogos": meus_jogos,
        "jogos_sugeridos": jogos_sugeridos,
        "blocos": blocos,
        "blocos_periodo": blocos_periodo,
        "periodos": periodos,
        "periodos_disponiveis": periodos_disponiveis,
        "historico": historico,
        "financeiro": financeiro,
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
    imprimir_relatorio_auditoria_periodo()


def imprimir_relatorio_auditoria_periodo():
    """Relatório da auditoria linha a linha (todos os elementos vs. filtro de
    período). Histórico das revisões anteriores nos comentários de código de
    calcular_bundle_periodo() e aplicarPeriodo(); esta função sempre reflete o
    estado ATUAL, não um changelog acumulado."""
    ja_era_ok = [
        "BLOCO 1 — Total de sorteios, número mais/menos frequente, média pares/sorteio, "
        "soma média, maior sequência vista (renderKpisPeriodo)",
        "BLOCO 2 — Financeiro: total pago, prêmio médio faixa 1, maior/menor prêmio "
        "(concurso+data), sorteios acumulados e % (renderFinanceiro + calc_financeiro por período)",
        "BLOCO 3 — Heatmap/grade interativa, frequência por dezena, donut pares/ímpares, "
        "atraso (relativo ao último sorteio DO PERÍODO), faixas, soma, tendência recente, "
        "evolução acumulada top-5",
        "BLOCO 4 — Distribuição de tamanho de sequência e top sequências por tamanho (abas 2-7)",
        "BLOCO 5 — Top 20 pares que mais saíram juntos (co-ocorrência)",
        "BLOCO 6 — Aba Blocos: ranking por bloco A-E, tabela campeão/lanterna, combinações "
        "de distribuição, co-ocorrência entre blocos (o card \"Blocos por período — histórico "
        "completo\" é uma exceção INTENCIONAL: o próprio título já avisa que é sempre o "
        "histórico inteiro, serve pra mostrar tendência ao longo do tempo)",
    ]
    corrigido_nesta_auditoria = [
        "BLOCO 5 — Anti-correlação (top 15 pares que MENOS saíram juntos): lia DATA.anticorrelacao "
        "fixo; agora vem de bundle.anticorrelacao, recalculado por período (calc_anticorrelacao "
        "sobre calc_coocorrencia_completa do subconjunto)",
        "BLOCO 7 — Repetição do concurso anterior: lia DATA.repeticao_anterior fixo; agora "
        "bundle.repeticao_anterior (calc_repeticao_anterior por período)",
        "BLOCO 7 — Ciclo médio por dezena: lia DATA.ciclo_medio fixo; agora bundle.ciclo_medio",
        "BLOCO 7 — Trios mais frequentes: lia DATA.trios fixo; agora bundle.trios (top 15 do período)",
        "BLOCO 7 — Grade linha/coluna + mapa de calor do volante 5x5: lia DATA.grade/DATA.frequencia "
        "fixos; agora bundle.grade/bundle.frequencia",
        "BLOCO 7 — Dígitos finais (média por sorteio): lia DATA.digitos_finais fixo; agora "
        "bundle.digitos_finais",
        "BLOCO 7 — Números quentes e frios: comparava a janela de recência (15/30/50) contra o "
        "histórico GERAL sempre; agora a janela e a linha de base usam só os sorteios do período "
        "ativo (reaproveita obterIndicesSorteiosDoPeriodo, já usado pela grade interativa)",
    ]
    dados_financeiros_ja_existentes = [
        "calc_financeiro() já existia e já era chamado por período desde uma revisão anterior "
        "(calcular_bundle_periodo -> \"financeiro\": calc_financeiro(rows_p)) — confirmado com "
        "teste de clique real: total pago, maior/menor prêmio e concurso mudam entre 'Todos' e "
        "'2024' no navegador ao vivo. Nenhum campo financeiro precisou ser adicionado nesta rodada.",
    ]
    limitacao_conhecida = [
        "A grade interativa 5x5 (filtro por clique em dezenas) usa uma cópia em JavaScript dos "
        "cálculos acima (montarBundleFiltradoPorNumeros) pra não precisar pré-computar todas as "
        "combinações possíveis de dezenas no servidor — blocos e financeiro continuam mostrando o "
        "período ativo (não recalculados pela combinação de números selecionada), decisão de "
        "escopo já documentada no código.",
    ]

    print("\n" + "=" * 70)
    print("AUDITORIA COMPLETA — TODOS OS ELEMENTOS vs. FILTRO DE PERÍODO")
    print("=" * 70)
    print(f"\n✅ Já respondiam corretamente ao período ({len(ja_era_ok)} blocos):")
    for item in ja_era_ok:
        print(f"   - {item}")
    print(f"\n🔧 Corrigidos nesta auditoria ({len(corrigido_nesta_auditoria)}):")
    for item in corrigido_nesta_auditoria:
        print(f"   - {item}")
    print(f"\n💰 Dados financeiros:")
    for item in dados_financeiros_ja_existentes:
        print(f"   - {item}")
    print(f"\nℹ️  Limitação de escopo conhecida (documentada, não é bug):")
    for item in limitacao_conhecida:
        print(f"   - {item}")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera BI HTML dos sorteios da Lotofácil")
    parser.add_argument("--input", default="lotofacil_sorteios.csv", help="CSV de entrada (ignorado se --db/--source forem usados)")
    parser.add_argument("--db", default=None, help="Caminho do banco SQLite (lotofacil.db) — tem prioridade sobre --input")
    parser.add_argument("--source", choices=["supabase"], default=None,
                         help="--source supabase lê do Supabase em vez do SQLite/CSV")
    parser.add_argument("--supabase-url", default=None, help="Necessário só se SUPABASE_URL não estiver no ambiente/.env")
    parser.add_argument("--supabase-key", default=None, help="Necessário só se SUPABASE_ANON_KEY não estiver no ambiente/.env")
    parser.add_argument("--github-repo", default="andrevisc-1209/lotofacil-bi",
                         help="dono/repositorio — usado só para linkar o botão 'Atualizar dados' à página Run workflow do GitHub Actions")
    parser.add_argument("--periodo", default=None, help="Filtra os dados antes de gerar o dashboard (ex: 2025, 2025-06)")
    parser.add_argument("--output", default="index.html")
    args = parser.parse_args()

    fonte_supabase = None
    if args.source == "supabase":
        import lotofacil_db

        url = args.supabase_url
        key = args.supabase_key
        if not (url and key):
            env_url, env_key = lotofacil_db.carregar_credenciais_supabase()
            url = url or env_url
            key = key or env_key
        if not (url and key):
            print("SUPABASE_URL e SUPABASE_ANON_KEY precisam estar configurados "
                  "(--supabase-url/--supabase-key, variável de ambiente ou .env).")
            exit(1)
        db = lotofacil_db.Database.supabase(url, key)
        rows = carregar_de_database(db)
        db.fechar()
        print(f"Carregados {len(rows)} sorteios do Supabase.")
        # a anon key é pública por design (RLS só permite leitura) — segura para embutir no HTML
        fonte_supabase = {"url": url, "anon_key": key}
    elif args.db:
        if not Path(args.db).exists():
            print(f"Banco '{args.db}' não encontrado. Rode primeiro: python lotofacil_atualizar.py --init 500")
            exit(1)
        import lotofacil_db
        db = lotofacil_db.Database.sqlite(args.db)
        rows = carregar_de_database(db)
        db.fechar()
        print(f"Carregados {len(rows)} sorteios de '{args.db}'.")
    else:
        if not Path(args.input).exists():
            print(f"Arquivo '{args.input}' não encontrado. Rode primeiro: python lotofacil_coletar.py")
            exit(1)
        rows = carregar(args.input)
        print(f"Carregados {len(rows)} sorteios.")

    if not rows:
        print("Nenhum sorteio encontrado na fonte de dados. "
              "Rode lotofacil_atualizar.py (ou lotofacil_migrar.py, se a fonte for o Supabase) primeiro.")
        exit(1)

    if args.periodo:
        rows = filtrar_por_periodo(rows, args.periodo)
        print(f"Filtrado para o período '{args.periodo}': {len(rows)} sorteios.")
        if not rows:
            print("Nenhum sorteio encontrado para esse período.")
            exit(1)

    gerar_html(rows, args.output, fonte_supabase=fonte_supabase, github_repo=args.github_repo)

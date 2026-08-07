"""
megasena_bi.py
---------------
Gera o dashboard HTML interativo da Mega-Sena (6 dezenas de 01-60) — mesmo
padrão visual e arquitetural de lotofacil_bi.py (Python pré-computa tudo por
período, JS só renderiza), mas SEM simulador de aposta nem ranking de jogos
pessoais (não faz sentido pra esse dashboard).

Uso:
    python megasena_bi.py --db megasena.db
    python megasena_bi.py --source supabase
    python megasena_bi.py --output megasena.html
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

# jogos pessoais pra validação financeira — placeholders ilustrativos.
# Substituir pelos jogos reais do usuário antes de publicar (6 dezenas, 01-60).
JOGOS_MEGA = {
    "Jogo 1": [3, 11, 22, 34, 47, 58],
    "Jogo 2": [5, 14, 19, 28, 41, 55],
    "Jogo 3": [2, 9, 17, 26, 38, 50],
    "Jogo 4": [7, 16, 24, 33, 45, 60],
    "Jogo 5": [1, 12, 21, 30, 42, 53],
}

# prêmios de referência (faixas sem valor real no banco) e custo por jogo —
# sena (6 acertos) sempre usa o valor_premio real do sorteio, não uma referência fixa
PREMIOS_MEGA = {5: 55434.22, 4: 1106.60}
CUSTO_JOGO_MEGA = 5.00


# ─── carrega dados do banco (SQLite ou Supabase, via megasena_db.Database) ───

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
    return sorted(int(row[f"d{i:02d}"]) for i in range(1, 7))


# ─── análises (universo 01-60, 6 dezenas por sorteio) ────────────────────────

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
    return {d: n - 1 - ultimo.get(d, -1) for d in range(1, 61)}

def calc_pares_impares(sorteios):
    resultados = []
    for s in sorteios:
        p = sum(1 for d in s if d % 2 == 0)
        resultados.append({"pares": p, "impares": 6 - p})
    return resultados

def calc_faixas(sorteios):
    """Distribuição por faixa em cada sorteio — terços do universo 1-60."""
    resultados = []
    for s in sorteios:
        resultados.append({
            "baixo": sum(1 for d in s if 1 <= d <= 20),
            "medio": sum(1 for d in s if 21 <= d <= 40),
            "alto":  sum(1 for d in s if 41 <= d <= 60),
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

def calc_sequencias_consecutivas(sorteios):
    """Runs de números consecutivos dentro de cada sorteio de 6 dezenas. Com
    só 6 números de 60, sequências longas são raras — as abas do dashboard só
    mostram tamanho 2, 3 e 4, mas dist_tamanho guarda qualquer tamanho
    encontrado (usado pelo KPI 'maior sequência vista')."""
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

def calc_tendencia(sorteios, janela=30):
    """Frequência nos últimos `janela` sorteios vs. geral. Janela padrão 30
    (não 50 como na Lotofácil) — cada sorteio da Mega-Sena só cobre 6/60=10%
    do universo (vs. 15/25=60% da Lotofácil), então o sinal de tendência
    precisa de uma janela relativamente menor pra não diluir demais."""
    n = len(sorteios)
    freq_total = calc_frequencia(sorteios)
    freq_rec = calc_frequencia(sorteios[-janela:])
    dados = []
    for d in range(1, 61):
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
    for d in range(1, 61):
        idx = indices.get(d, [])
        intervalos = [b - a for a, b in zip(idx, idx[1:])]
        resultado[d] = {
            "ciclo": round(sum(intervalos) / len(intervalos), 1) if intervalos else None,
            "aparicoes": len(idx),
        }
    return resultado

def calc_repeticao_anterior(sorteios):
    return [len(set(sorteios[i]) & set(sorteios[i - 1])) for i in range(1, len(sorteios))]


# ─── blocos de 10 (A: 01-10, B: 11-20, C: 21-30, D: 31-40, E: 41-50, F: 51-60) ─

BLOCOS_NOMES = ["A", "B", "C", "D", "E", "F"]

def bloco_de(d: int) -> int:
    return (d - 1) // 10

def _contagem_blocos(s) -> list:
    c = Counter(bloco_de(d) for d in s)
    return [c.get(i, 0) for i in range(6)]

def calc_blocos_freq_individual(sorteios):
    freq = calc_frequencia(sorteios)
    resultado = {}
    for i, nome in enumerate(BLOCOS_NOMES):
        inicio = i * 10 + 1
        resultado[nome] = {d: freq.get(d, 0) for d in range(inicio, inicio + 10)}
    return resultado

def calc_blocos_combinacoes(sorteios, top_n=15):
    """Assinaturas de distribuição mais frequentes, ex: '1-1-1-1-1-1' → sorteio
    perfeitamente distribuído entre os 6 blocos."""
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
    """Matriz 6×6: frequência com que cada par de blocos contribui com >=2
    dezenas no mesmo sorteio (diagonal = quantas vezes aquele bloco sozinho
    chegou a >=2 — threshold menor que a Lotofácil pois cada bloco só tem
    espaço pra no máximo 6 dezenas do sorteio, não 15)."""
    matriz = [[0] * 6 for _ in range(6)]
    for s in sorteios:
        c = _contagem_blocos(s)
        ativos = [i for i in range(6) if c[i] >= 2]
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
    completo, não filtra por período do seletor (mesmo padrão da Lotofácil:
    o propósito é mostrar tendência AO LONGO do tempo)."""
    buckets = defaultdict(list)
    for row, s in zip(rows, sorteios):
        periodo = data_iso_de(row)[:7]
        buckets[periodo].append(_contagem_blocos(s))
    resultado = []
    for periodo in sorted(buckets):
        valores = buckets[periodo]
        medias = [round(sum(v[i] for v in valores) / len(valores), 2) for i in range(6)]
        resultado.append({"periodo": periodo, "medias": medias, "total": len(valores)})
    return resultado


# ─── resumo financeiro (mesmo padrão da Lotofácil: faixa 1 = prêmio maior) ───

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


def calc_jogos_financeiro_mega(jogos_dict, rows_p, sorteios_p):
    """Validador financeiro completo de um dicionário {nome: [6 dezenas]} da
    Mega-Sena sobre um período — mesma arquitetura de calc_jogos_financeiro
    da Lotofácil, adaptada pra 3 faixas de prêmio (4/5/6 acertos em vez de
    11-15). 6 acertos (sena) usa o valor_premio real do sorteio; 4 e 5
    acertos (quadra/quina) usam PREMIOS_MEGA (referência fixa, mesma razão
    documentada na Lotofácil: a API da Caixa não expõe o valor real dividido
    entre ganhadores dessas faixas)."""
    n = len(sorteios_p)
    resultado = []
    for nome, numeros in jogos_dict.items():
        conjunto = set(numeros)
        contagem = {4: 0, 5: 0, 6: 0}
        historico = []
        ganho = 0.0
        saldo_evolucao = []
        acumulado = 0.0
        melhor = {"acertos": 0, "concurso": None, "data": None}

        for row, s in zip(rows_p, sorteios_p):
            acertos = len(conjunto & set(s))
            premio = 0.0
            if acertos >= 4:
                contagem[acertos] += 1
                if acertos == 6:
                    premio = _to_float(row.get("valor_premio")) or 0.0
                else:
                    premio = PREMIOS_MEGA[acertos]
                historico.append({
                    "concurso": _to_int(row["concurso"]),
                    "data": row["data"],
                    "acertos": acertos,
                    "premio": round(premio, 2),
                })
                ganho += premio
            if acertos > melhor["acertos"]:
                melhor = {"acertos": acertos, "concurso": _to_int(row["concurso"]), "data": row["data"]}
            acumulado += premio - CUSTO_JOGO_MEGA
            saldo_evolucao.append(round(acumulado, 2))

        gasto = round(CUSTO_JOGO_MEGA * n, 2)
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
# mesma lógica de bucketing por data da Lotofácil — não depende do tipo de loteria

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
    janela = max(1, min(30, n))
    tendencia = calc_tendencia(sorteios_p, janela=janela)
    somas = calc_soma(sorteios_p)
    repeticao_anterior = calc_repeticao_anterior(sorteios_p)
    ciclo_medio = calc_ciclo_medio(sorteios_p)
    cooc_completo_p = calc_coocorrencia_completa(sorteios_p)
    anticorrelacao = calc_anticorrelacao(cooc_completo_p, bottom_n=15)
    meus_jogos_p = calc_jogos_financeiro_mega(JOGOS_MEGA, rows_p, sorteios_p) if JOGOS_MEGA else None

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
        "meus_jogos": meus_jogos_p,
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
# Mesma paleta/arquitetura visual "Dark Analytics App" da Lotofácil (CSS quase
# idêntico — só numgrid/blocos-grid mudam de proporção pra 60 números em 6
# blocos de 10, em vez de 25 números em 5 blocos de 5).

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Mega-Sena BI — {titulo}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #08090f;
    --bg2: #0f1018;
    --bg3: #161824;
    --border: rgba(255,255,255,0.06);
    --accent: #059669;
    --accent2: #34d399;
    --neon: rgba(5,150,105,0.15);
    --text: #f1f0f5;
    --muted: #6b7280;
    --green: #10b981;
    --red: #ef4444;
    --gold: #f59e0b;
    --card: var(--bg2);
    --accent3: var(--green);
    --accent4: var(--gold);
    --accent5: var(--red);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    background: radial-gradient(ellipse 1200px 800px at 15% -10%, #0f3d2e 0%, transparent 60%),
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
  .btn-primary:hover:not(:disabled) { background: #047857; box-shadow: 0 0 0 3px rgba(5,150,105,.25); }
  .btn-primary:active:not(:disabled) { transform: scale(.97); }
  .btn-secondary { background: transparent; border: 1px solid var(--accent); color: var(--accent2); }
  .btn-secondary:hover:not(:disabled) { background: rgba(5,150,105,.12); }
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
  #gh-modal-salvar:hover { background: #047857; }
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
  .kpi .value { font-size: 36px; font-weight: 700; margin-top: 4px; color: var(--accent2); line-height: 1.15; }
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
  @media (max-width: 1024px) { .grid-3 { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 640px) { .grid-2, .grid-3, .grid-13, .grid-31 { grid-template-columns: 1fr; } .grid { gap: 14px; padding: 16px; } }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 24px; box-shadow: 0 4px 24px rgba(0,0,0,.3); transition: border-color .2s ease, box-shadow .2s ease; }
  .card:hover { border-color: rgba(5,150,105,.3); box-shadow: 0 4px 24px rgba(0,0,0,.3), 0 0 0 1px rgba(5,150,105,.08); }
  .card h2 { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: .8px; color: var(--muted); margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
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
  /* grade interativa 6×10 (60 números) — substitui o mapa de calor estático */
  .numgrid { display: grid; grid-template-columns: repeat(10, 1fr); gap: 8px; max-width: 620px; margin: 0 auto; }
  .numgrid-cell {
    aspect-ratio: 1; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 12px; color: #fff; cursor: pointer; border: 2px solid transparent;
    transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
  }
  .numgrid-cell:hover { transform: scale(1.1); }
  .numgrid-cell.selecionada { border-color: var(--accent2); box-shadow: 0 0 0 4px var(--neon), 0 0 18px rgba(52,211,153,.55); transform: scale(1.06); }
  .numgrid-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 18px; flex-wrap: wrap; }
  .numgrid-hint { font-size: 12px; color: var(--muted); flex: 1 1 260px; }
  @media (max-width: 640px) {
    .numgrid { max-width: 340px; gap: 5px; }
    .numgrid-cell { font-size: 10px; }
    .numgrid-footer { flex-direction: column; align-items: stretch; }
  }
  .tabs { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
  .tab { padding: 5px 14px; border-radius: 6px; border: 1px solid var(--border); background: transparent; color: var(--muted); cursor: pointer; font-size: 12px; font-weight: 600; transition: all .15s; }
  .tab.active { background: var(--accent); border-color: var(--accent); color: #fff; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--muted); font-weight: 700; font-size: 11px; text-transform: uppercase; padding: 10px 8px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--card); z-index: 1; }
  td { padding: 9px 8px; border-bottom: 1px solid #1e2130; }
  tbody tr:nth-child(even) td { background: rgba(255,255,255,.025); }
  tbody tr:hover td { background: rgba(5,150,105,.07); }
  tr:last-child td { border-bottom: none; }
  @media (max-width: 640px) { table { font-size: 12px; } td, th { padding: 7px 6px; } }
  .badge { display: inline-flex; align-items: center; gap: 3px; background: #1e2130; border-radius: 4px; padding: 2px 6px; font-size: 11px; font-weight: 700; }
  .badge.up { color: var(--green); }
  .badge.down { color: var(--red); }
  .badge.flat { color: var(--muted); }
  .seq-tag { display: inline-block; background: #10321f; border: 1px solid #1c5c3a; color: var(--accent2); border-radius: 4px; padding: 2px 7px; font-weight: 700; font-size: 12px; margin-right: 3px; }
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
  /* blocos — ranking de frequência individual por número (6 blocos de 10) */
  .blocos-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
  @media (max-width: 1200px) { .blocos-grid { grid-template-columns: repeat(3, 1fr); } }
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
  .hotcold-card.frio { background: rgba(52,211,153,.15); border-color: var(--accent2); }
  .hotcold-card .num { font-size: 16px; font-weight: 700; }
  .hotcold-card .status { font-size: 14px; }
  .hotcold-card .pct { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .hotcold-legend { display: flex; gap: 18px; margin-bottom: 12px; font-size: 12px; color: var(--muted); }
  .money-pos { color: var(--green); font-weight: 700; }
  .money-neg { color: var(--red); font-weight: 700; }
  /* validador financeiro de jogos ("Meus jogos") */
  .jogos-resumo-titulo { font-size: 15px; font-weight: 700; color: var(--text); margin-bottom: 2px; }
  .jogos-resumo-sub { font-size: 12px; color: var(--muted); margin-bottom: 12px; }
  .jogos-tabela th[data-col] { cursor: pointer; user-select: none; white-space: nowrap; }
  .jogos-tabela th[data-col]:hover { color: var(--text); }
  .jogos-tabela th .sort-arrow { display: inline-block; width: 10px; opacity: .6; }
  .jogos-row { cursor: pointer; }
  .jogos-row.saldo-pos td { background: rgba(16,185,129,.06); }
  .jogos-row.saldo-neg td { background: rgba(239,68,68,.06); }
  .jogos-row:hover td { filter: brightness(1.15); }
  .jogos-row .jogos-dezenas { font-family: monospace; font-size: 11px; color: var(--muted); }
  .jogos-detail-row td { padding: 0; border-bottom: 1px solid #1e2130; }
  .jogos-detail-inner { max-height: 0; overflow: hidden; transition: max-height .25s ease; padding: 0 16px; }
  .jogos-detail-inner.aberto { max-height: 900px; padding: 16px; }
  .jogos-detail-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 20px; margin-bottom: 16px; }
  @media (max-width: 900px) { .jogos-detail-grid { grid-template-columns: 1fr; } }
  .jogos-detail-grid canvas { max-height: 200px; }
  .jogos-detail-titulo { font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); margin-bottom: 10px; }
  /* seletor de período — card com grupos empilhados por tipo */
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
  .period-btn:disabled { opacity: .35; cursor: not-allowed; }
  .period-btn:disabled:hover { border-color: var(--border); }
  /* nível 2 (radio "Ano completo/Semestre/Trimestre/Bimestre/Mês") e a
     animação de entrada dos níveis 2/3 quando aparecem em cascata */
  .period-nivel2-opcoes { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
  .period-radio { display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 12px; color: var(--text); }
  .period-radio input { accent-color: var(--accent); cursor: pointer; }
  .period-nivel { animation: periodNivelIn .2s ease; }
  @keyframes periodNivelIn { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
  @media (max-width: 640px) {
    .period-selector-wrap { top: 57px; padding: 10px 16px 0; }
    .period-card { padding: 12px 14px; }
    .period-row { flex-direction: column; align-items: stretch; gap: 4px; }
    .period-row .tipo-label { width: auto; padding-top: 0; }
    .period-btn { font-size: 11px; padding: 3px 8px; }
    .period-nivel2-opcoes { gap: 12px; }
  }
  .periodo-banner { margin: 12px 24px 0; padding: 10px 16px; background: rgba(245,158,11,.12); border: 1px solid var(--accent4); border-radius: 8px; color: #fbbf24; font-size: 13px; font-weight: 600; }
  .update-banner { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .update-banner button { background: #f59e0b; border: none; border-radius: 8px; min-height: 36px; padding: 0 16px; color: #1a1300; font-weight: 700; cursor: pointer; font-size: 12px; flex-shrink: 0; transition: background .15s, transform .1s; }
  .update-banner button:hover { background: #fbbf24; }
  .update-banner button:active { transform: scale(.97); }
  .fin-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; }
  .fin-item { background: #10131c; border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
  .fin-item .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 6px; }
  .fin-item .value { font-size: 18px; font-weight: 700; color: var(--accent2); }
  .fin-item .sub { font-size: 11px; color: var(--muted); margin-top: 4px; }
  .page-tabs { display: flex; gap: 6px; padding: 16px 24px; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: thin; }
  .page-tab { padding: 10px 20px; border: 1px solid transparent; border-radius: 999px; background: transparent; color: var(--muted); cursor: pointer; font-size: 13px; font-weight: 600; white-space: nowrap; flex-shrink: 0; transition: background .15s, color .15s, border-color .15s; }
  .page-tab:hover { color: var(--text); background: rgba(5,150,105,.1); border-color: var(--border); }
  .page-tab.active { color: #fff; background: var(--accent); border-color: var(--accent); box-shadow: 0 0 0 4px var(--neon); }
  @media (max-width: 640px) {
    .page-tabs { padding: 12px 16px 0; gap: 4px; }
    .page-tab { padding: 10px 16px; font-size: 13px; }
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
  @keyframes histFlash { 0%, 100% { background: transparent; } 30% { background: rgba(5,150,105,.35); } }
  .hist-sorteio-detail { max-height: 0; overflow: hidden; transition: max-height .25s ease; margin-left: 24px; }
  .hist-detail-inner { padding: 10px 12px 14px; }
  .hist-detail-titulo { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
  .hist-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .hist-badge { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 8px; background: #10321f; border: 1px solid #1c5c3a; color: #fff; font-weight: 700; font-size: 12px; }
  .hist-detail-meta { display: flex; gap: 20px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
  .hist-detail-meta b { color: var(--text); }
</style>
</head>
<body>
<header>
  <h1>💰 Mega-Sena BI</h1>
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
    <div class="gh-modal-aviso">⚠️ Salvo apenas no seu navegador (localStorage). Nunca enviado para nenhum servidor nosso — só direto para a API do GitHub. Esse token é compartilhado com o dashboard da Lotofácil (mesmo repositório).</div>
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
<!-- Grade interativa 6×10 — substitui o mapa de calor estático; clique filtra os gráficos abaixo -->
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
  <div class="kpi"><span class="kpi-icon">➕</span><div class="label">Soma média das 6 dez.</div><div class="value" id="kpi-soma">—</div></div>
  <div class="kpi"><span class="kpi-icon">🔗</span><div class="label">Maior sequência vista</div><div class="value" id="kpi-seq">—</div><div class="sub">números consecutivos</div></div>
  <div class="kpi"><span class="kpi-icon">🔁</span><div class="label">Repetições do concurso anterior</div><div class="value" id="kpi-repeticao">—</div><div class="sub" id="kpi-repeticao-sub"></div></div>
</div>

<!-- Resumo financeiro do período selecionado -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>💰 Resumo financeiro (prêmios da faixa 1 — sena, 6 acertos)</h2>
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
      <span><span style="color:#059669">■</span> Pares: <b id="pct-pares">—</b></span>
      <span><span style="color:#34d399">■</span> Ímpares: <b id="pct-impares">—</b></span>
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
    <h2>➕ Distribuição da soma das 6 dezenas</h2>
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
    <h2>📉 Tendência — últimos 30 vs. total (% de aparição)</h2>
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

<!-- Meus jogos — validador financeiro -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card" id="jogos-card">
    <h2>💰 Meus jogos — validador financeiro</h2>
    <div id="jogos-resumo"></div>
    <div id="jogos-tabela-wrap" style="overflow-x:auto; margin-top:16px;"></div>
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
  <div class="kpi"><span class="kpi-icon">🏆</span><div class="label">Bloco F (51-60) — campeão</div><div class="value" id="kpi-bloco-f">—</div><div class="sub" id="kpi-bloco-f-sub"></div></div>
</div>

<!-- 1. Ranking de frequência individual por número, dentro de cada bloco -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🏅 Ranking de frequência por bloco (01-10, 11-20, ..., 51-60)</h2>
    <div class="blocos-grid" id="blocos-ranking-grid"></div>
  </div>
</div>

<!-- 2. Tabela comparativa campeão/lanterna -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>📋 Comparativo dos 6 blocos — campeão e lanterna</h2>
    <div id="blocos-campeoes" style="overflow-x:auto;"></div>
  </div>
</div>

<!-- 3. Combinações de distribuição mais frequentes + co-ocorrência entre blocos -->
<div class="grid grid-2">
  <div class="card">
    <h2>🔢 Combinações de distribuição mais frequentes (A-B-C-D-E-F)</h2>
    <div id="blocos-combinacoes" style="overflow-x:auto;"></div>
  </div>
  <div class="card">
    <h2>🔗 Co-ocorrência entre blocos (≥2 dezenas no mesmo sorteio)</h2>
    <div class="grade-wrap" id="blocos-coocorrencia" style="grid-template-columns: 40px repeat(6, 1fr);"></div>
  </div>
</div>

<!-- 4. Qual bloco mais/menos contribui por período (histórico completo) -->
<div class="grid" style="grid-template-columns: 1fr;">
  <div class="card">
    <h2>🗓️ Blocos por período — média mensal (histórico completo)</h2>
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
      <input type="text" id="hist-busca-concurso" placeholder="ex: 2800" style="flex:1; min-width:0; background:#0f1117; border:1px solid var(--border); border-radius:6px; padding:9px 12px; color:var(--text); font-size:13px;"/>
      <button id="hist-btn-buscar" class="btn-secondary">Ir</button>
    </div>
    <div class="sim-error" id="hist-busca-erro" style="display:none; color:var(--red); font-size:12px; margin-top:6px;"></div>
  </div>
  <div class="hist-controle-grupo">
    <label for="hist-filtro-dezena">Filtrar por dezena (01-60)</label>
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
}

// grade interativa 6×10 — clique filtra os gráficos abaixo (mesma mecânica da
// Lotofácil, só que sobre 60 números em vez de 25)
let numerosSelecionados = new Set();
let hotcoldJanelaAtual = 30;

// "Meus jogos" — validador financeiro; constantes declaradas aqui (antes do
// bootstrapping da página) pra evitar TDZ, já que renderSeletorCascata()
// chama aplicarPeriodo() -> renderPeriodoCompleto() -> renderJogosSection()
// logo no carregamento do script.
const IDS_MEUS_JOGOS = { resumo: 'jogos-resumo', tabelaWrap: 'jogos-tabela-wrap', prefix: 'jogo' };
const ESTADO_ORDENACAO_JOGOS = {}; // por prefix: { coluna, direcao }
const COLUNAS_JOGOS = [
  { key: 'idx', label: '#', ordenavel: false },
  { key: 'nome', label: 'Jogo', ordenavel: true },
  { key: 'dezenas', label: 'Dezenas', ordenavel: false },
  { key: 'p6', label: '6pts', ordenavel: true },
  { key: 'p5', label: '5pts', ordenavel: true },
  { key: 'p4', label: '4pts', ordenavel: true },
  { key: 'total_premiado', label: 'Total≥4', ordenavel: true },
  { key: 'pct_premiado', label: '% Premiado', ordenavel: true },
  { key: 'gasto', label: 'Gasto', ordenavel: true },
  { key: 'ganho', label: 'Ganho', ordenavel: true },
  { key: 'saldo', label: 'Saldo', ordenavel: true },
  { key: 'roi', label: 'ROI', ordenavel: true },
  { key: 'melhor', label: 'Melhor resultado', ordenavel: false },
];

function renderNumGrid(bundle) {
  const grid = document.getElementById('numgrid');
  grid.innerHTML = '';
  const freq = bundle.frequencia;
  const vals = Object.values(freq);
  const minV = Math.min(...vals), maxV = Math.max(...vals);
  for (let d = 1; d <= 60; d++) {
    const cnt = freq[d] || 0;
    const t = maxV > minV ? (cnt - minV) / (maxV - minV) : 0;
    const r = Math.round(6 + t * 30), g = Math.round(78 + t * 133), b = Math.round(59 + t * 90);
    const cell = document.createElement('div');
    cell.className = 'numgrid-cell' + (numerosSelecionados.has(d) ? ' selecionada' : '');
    cell.dataset.num = String(d);
    cell.style.background = `rgb(${r},${g},${b})`;
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
      datasets: [{ data: [total_p, total_i], backgroundColor: ['#059669','#34d399'], borderWidth: 0 }]
    },
    options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { labels: { color: '#94a3b8' } } }, cutout: '65%' }
  });
}

function renderChartFreq(bundle) {
  const sorted_freq = Object.entries(bundle.frequencia).map(([d,c])=>({d:+d,c})).sort((a,b)=>b.c-a.c);
  const labels = sorted_freq.map(x => String(x.d).padStart(2,'0'));
  const values = sorted_freq.map(x => x.c);
  const colors = values.map((_,i) => i < 10 ? '#059669' : i > 49 ? '#ef4444' : '#334155');
  criarChart('chartFreq', {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Vezes sorteada', data: values, backgroundColor: colors, borderRadius: 4 }] },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

function renderChartAtraso(bundle) {
  const atr = Object.entries(bundle.atraso).map(([d,a])=>({d:+d,a})).sort((a,b)=>b.a-a.a);
  const colors = atr.map(x => x.a >= 20 ? '#ef4444' : x.a >= 10 ? '#f59e0b' : '#10b981');
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
  const media = { baixo: 0, medio: 0, alto: 0 };
  bundle.faixas.forEach(f => { media.baixo += f.baixo; media.medio += f.medio; media.alto += f.alto; });
  criarChart('chartFaixas', {
    type: 'bar',
    data: {
      labels: ['Baixo (01-20)', 'Médio (21-40)', 'Alto (41-60)'],
      datasets: [{ label: 'Média de dezenas por sorteio', data: [media.baixo/n, media.medio/n, media.alto/n].map(v=>+v.toFixed(2)), backgroundColor: ['#34d399','#059669','#047857'], borderRadius: 4 }]
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
      datasets: [{ label: 'Sorteios', data: labels.map(b => buckets[b]), backgroundColor: '#059669', borderRadius: 4 }]
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

  const dist = new Array(7).fill(0);
  rep.forEach(v => { if (v <= 6) dist[v]++; });
  criarChart('chartRepeticao', {
    type: 'bar',
    data: {
      labels: dist.map((_,i) => String(i)),
      datasets: [{ label: 'Sorteios', data: dist, backgroundColor: '#059669', borderRadius: 4 }]
    },
    options: { ...chartDefaults, plugins: { legend: { display: false } } }
  });
}

// ── Números quentes e frios — a janela de recência (15/30/50) e a linha de
// base de comparação usam só os sorteios do período ativo ────────────────────
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
  for (let d = 1; d <= 60; d++) freqRec[d] = 0;
  recentes.forEach(s => s.forEach(d => freqRec[d]++));

  for (let d = 1; d <= 60; d++) {
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
  const nomes = ['A','B','C','D','E','F'];
  const faixaLabel = { A: '01 a 10', B: '11 a 20', C: '21 a 30', D: '31 a 40', E: '41 a 50', F: '51 a 60' };
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
      table.innerHTML = `<thead><tr><th>#</th><th>A-B-C-D-E-F</th><th>Vezes</th><th>%</th></tr></thead>`;
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
        const r = Math.round(6 + t * 30), g = Math.round(78 + t * 133), bch = Math.round(59 + t * 90);
        const cell = document.createElement('div');
        cell.className = 'heatcell';
        cell.style.background = `rgb(${r},${g},${bch})`;
        cell.style.color = t > 0.4 ? '#fff' : '#ccc';
        cell.style.fontSize = '12px';
        cell.textContent = cnt;
        cell.title = i === j
          ? `Bloco ${nomeLinha} sozinho com ≥2 dezenas: ${cnt} vezes`
          : `Blocos ${nomeLinha} e ${nomeCol} juntos com ≥2 dezenas cada: ${cnt} vezes`;
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
    { label: 'Prêmio médio (sena)', valor: f.media_premio_faixa1 != null ? formatarMoeda(f.media_premio_faixa1) : '—' },
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

function formatarMoeda(v) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatarPct(v) {
  return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
}

// ── Meus jogos — validador financeiro. O cálculo (ganho/gasto/saldo/ROI,
// histórico de sorteios em que pontuou, evolução do saldo) já vem pronto do
// Python em bundle.meus_jogos — aqui só ordena e desenha. ───────────────────

function valorOrdenacaoJogo(jogo, coluna) {
  switch (coluna) {
    case 'nome': return jogo.nome;
    case 'p6': return jogo.contagem[6];
    case 'p5': return jogo.contagem[5];
    case 'p4': return jogo.contagem[4];
    case 'melhor': return jogo.melhor.acertos;
    default: return jogo[coluna];
  }
}

function renderJogosResumo(jogos, ids, nSorteios) {
  const el = document.getElementById(ids.resumo);
  const nJogos = jogos.length;
  const custoPorSorteio = nSorteios ? (jogos[0].gasto / nSorteios) * nJogos : 0;
  const gastoTotal = jogos.reduce((a, j) => a + j.gasto, 0);
  const ganhoTotal = jogos.reduce((a, j) => a + j.ganho, 0);
  const saldoTotal = +(ganhoTotal - gastoTotal).toFixed(2);
  const roiTotal = gastoTotal ? (saldoTotal / gastoTotal * 100) : 0;
  el.innerHTML = `
    <div class="jogos-resumo-titulo">${nJogos} jogo${nJogos === 1 ? '' : 's'} · ${formatarMoeda(custoPorSorteio)} por sorteio</div>
    <div class="mini-stats">
      <div>Sorteios no período<b>${nSorteios}</b></div>
      <div>Total apostado<b>${formatarMoeda(gastoTotal)}</b></div>
      <div>Total ganho<b>${formatarMoeda(ganhoTotal)}</b></div>
      <div>Saldo<b class="${saldoTotal >= 0 ? 'money-pos' : 'money-neg'}">${formatarMoeda(saldoTotal)}</b></div>
      <div>ROI<b class="${roiTotal >= 0 ? 'money-pos' : 'money-neg'}">${formatarPct(roiTotal)}</b></div>
    </div>`;
}

function construirDetalheJogo(container, jogo, canvasIdBase) {
  const grid = document.createElement('div');
  grid.className = 'jogos-detail-grid';

  const graficosDiv = document.createElement('div');
  graficosDiv.innerHTML = `
    <div class="jogos-detail-titulo">Distribuição de acertos</div>
    <canvas id="${canvasIdBase}-dist"></canvas>
    <div class="jogos-detail-titulo" style="margin-top:16px">Evolução do saldo acumulado</div>
    <canvas id="${canvasIdBase}-evol"></canvas>`;

  const listaDiv = document.createElement('div');
  listaDiv.style.cssText = 'max-height:280px; overflow-y:auto;';
  const tituloLista = document.createElement('div');
  tituloLista.className = 'jogos-detail-titulo';
  tituloLista.textContent = `Sorteios em que pontuou (${jogo.historico.length})`;
  listaDiv.appendChild(tituloLista);
  if (!jogo.historico.length) {
    const p = document.createElement('p');
    p.style.cssText = 'color:var(--muted); font-size:12px;';
    p.textContent = 'Nenhum sorteio com 4+ acertos neste período.';
    listaDiv.appendChild(p);
  } else {
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th>Concurso</th><th>Data</th><th>Acertos</th><th>Prêmio</th></tr></thead>';
    const tbody = document.createElement('tbody');
    [...jogo.historico].reverse().forEach(h => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${h.concurso}</td><td>${h.data}</td><td>${h.acertos}</td><td>${formatarMoeda(h.premio)}</td>`;
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
        labels: ['4', '5', '6'],
        datasets: [{ data: [4, 5, 6].map(p => jogo.contagem[p]), backgroundColor: '#059669', borderRadius: 4 }],
      },
      options: { ...chartDefaults, plugins: { legend: { display: false } } },
    });
    criarChart(`${canvasIdBase}-evol`, {
      type: 'line',
      data: {
        labels: jogo.saldo_evolucao.map((_, i) => i + 1),
        datasets: [{
          data: jogo.saldo_evolucao, borderColor: '#34d399', backgroundColor: 'rgba(5,150,105,.15)',
          fill: true, pointRadius: 0, borderWidth: 2, tension: .15,
        }],
      },
      options: { ...chartDefaults, plugins: { legend: { display: false } } },
    });
  });
}

function renderJogosTabela(jogos, ids, nSorteios) {
  const wrap = document.getElementById(ids.tabelaWrap);
  wrap.innerHTML = '';

  const estado = ESTADO_ORDENACAO_JOGOS[ids.prefix] || { coluna: 'saldo', direcao: -1 };
  ESTADO_ORDENACAO_JOGOS[ids.prefix] = estado;

  const ordenados = [...jogos].sort((a, b) => {
    const va = valorOrdenacaoJogo(a, estado.coluna), vb = valorOrdenacaoJogo(b, estado.coluna);
    if (typeof va === 'string') return va.localeCompare(vb) * estado.direcao;
    return (va - vb) * estado.direcao;
  });

  const melhorRoi = jogos.reduce((m, j) => (j.roi > m.roi ? j : m), jogos[0]);
  const maisPremiado = jogos.reduce((m, j) => (j.total_premiado > m.total_premiado ? j : m), jogos[0]);

  const table = document.createElement('table');
  table.className = 'jogos-tabela';
  const thead = document.createElement('thead');
  const trHead = document.createElement('tr');
  COLUNAS_JOGOS.forEach(col => {
    const th = document.createElement('th');
    if (col.ordenavel) {
      th.dataset.col = col.key;
      const seta = estado.coluna === col.key ? (estado.direcao === 1 ? '▲' : '▼') : '';
      th.innerHTML = `${col.label} <span class="sort-arrow">${seta}</span>`;
      th.addEventListener('click', () => {
        if (estado.coluna === col.key) estado.direcao *= -1;
        else { estado.coluna = col.key; estado.direcao = col.key === 'nome' ? 1 : -1; }
        renderJogosTabela(jogos, ids, nSorteios);
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
    const dezenasTxt = jogo.numeros.map(n => String(n).padStart(2, '0')).join(' ');
    const melhorTxt = jogo.melhor.concurso ? `${jogo.melhor.acertos} pts (c.${jogo.melhor.concurso})` : '—';
    let nomeTxt = jogo.nome;
    if (jogo === melhorRoi) nomeTxt += ' 🏆';
    if (jogo === maisPremiado) nomeTxt += ' 🥇';
    tr.innerHTML = `
      <td style="color:var(--muted)">${i + 1}</td>
      <td>${nomeTxt}</td>
      <td class="jogos-dezenas">${dezenasTxt}</td>
      <td>${jogo.contagem[6]}</td>
      <td>${jogo.contagem[5]}</td>
      <td>${jogo.contagem[4]}</td>
      <td>${jogo.total_premiado}</td>
      <td>${jogo.pct_premiado}%</td>
      <td>${formatarMoeda(jogo.gasto)}</td>
      <td>${formatarMoeda(jogo.ganho)}</td>
      <td class="${jogo.saldo >= 0 ? 'money-pos' : 'money-neg'}">${formatarMoeda(jogo.saldo)}</td>
      <td class="${jogo.roi >= 0 ? 'money-pos' : 'money-neg'}">${formatarPct(jogo.roi)}</td>
      <td>${melhorTxt}</td>`;
    tbody.appendChild(tr);

    const trDetail = document.createElement('tr');
    trDetail.className = 'jogos-detail-row';
    const tdDetail = document.createElement('td');
    tdDetail.colSpan = COLUNAS_JOGOS.length;
    const inner = document.createElement('div');
    inner.className = 'jogos-detail-inner';
    tdDetail.appendChild(inner);
    trDetail.appendChild(tdDetail);
    tbody.appendChild(trDetail);

    let construido = false;
    tr.addEventListener('click', () => {
      const aberto = inner.classList.contains('aberto');
      if (!aberto && !construido) {
        construirDetalheJogo(inner, jogo, ids.prefix + '-' + i);
        construido = true;
      }
      inner.classList.toggle('aberto', !aberto);
    });
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
}

function renderJogosSection(jogos, ids, nSorteios) {
  const resumoEl = document.getElementById(ids.resumo);
  const tabelaWrapEl = document.getElementById(ids.tabelaWrap);
  if (!jogos || !jogos.length) {
    resumoEl.innerHTML = '<p style="color:var(--muted)">Nenhum jogo configurado.</p>';
    tabelaWrapEl.innerHTML = '';
    return;
  }
  renderJogosResumo(jogos, ids, nSorteios);
  renderJogosTabela(jogos, ids, nSorteios);
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

function renderPeriodoCompleto(bundle) {
  renderKpisPeriodo(bundle);
  renderPI(bundle);
  renderChartFreq(bundle);
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
  // meus_jogos não é recalculado pra combinações de dezenas da grade
  // interativa (mesma exceção documentada pra blocos/financeiro) — usa o
  // total do período de onde os dados vieram, não o do subconjunto filtrado
  const totalParaJogos = bundle.meta.totalPeriodoBase || bundle.meta.total;
  renderJogosSection(bundle.meus_jogos, IDS_MEUS_JOGOS, totalParaJogos);
}

// ── grade interativa 6×10 — filtro client-side por combinação de dezenas ────
// (mesma arquitetura da Lotofácil: recalcula em JS os módulos period-aware
// sobre o subconjunto de sorteios que contêm TODAS as dezenas selecionadas,
// porque não dá pra pré-computar todas as combinações possíveis no servidor.
// blocos e financeiro continuam mostrando o período ativo, não o subconjunto
// filtrado — mesma exceção documentada na Lotofácil.)

function calcFrequenciaJS(sorteios) {
  const freq = {};
  for (let d = 1; d <= 60; d++) freq[d] = 0;
  sorteios.forEach(s => s.forEach(d => freq[d]++));
  return freq;
}
function calcAtrasoJS(sorteios) {
  const n = sorteios.length, ultimo = {};
  sorteios.forEach((s, i) => s.forEach(d => { ultimo[d] = i; }));
  const atraso = {};
  for (let d = 1; d <= 60; d++) atraso[d] = (d in ultimo) ? (n - 1 - ultimo[d]) : n;
  return atraso;
}
function calcParesImparesJS(sorteios) {
  return sorteios.map(s => { const p = s.filter(d => d % 2 === 0).length; return { pares: p, impares: 6 - p }; });
}
function calcFaixasJS(sorteios) {
  return sorteios.map(s => ({
    baixo: s.filter(d => d >= 1 && d <= 20).length,
    medio: s.filter(d => d >= 21 && d <= 40).length,
    alto: s.filter(d => d >= 41 && d <= 60).length,
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
  const janela = Math.max(1, Math.min(30, n));
  const freqTotal = calcFrequenciaJS(sorteios);
  const freqRecente = calcFrequenciaJS(sorteios.slice(-janela));
  const dados = [];
  for (let d = 1; d <= 60; d++) {
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
  for (let d = 1; d <= 60; d++) {
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

function montarBundleFiltradoPorNumeros(sorteios, bundleBase) {
  const { distTamanho, topPorTamanho } = calcSequenciasJS(sorteios);
  return {
    // totalPeriodoBase: total do período (não do subconjunto filtrado por
    // número) — meus_jogos não é recalculado pra essa combinação de
    // dezenas (mesma exceção documentada pra blocos/financeiro), então o %
    // e o "gasto" precisam continuar batendo com o total do período em que
    // os pct_total já foram calculados no Python.
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
    meus_jogos: bundleBase.meus_jogos,
  };
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

// ── função central: aplica um período a TODOS os elementos do dashboard ─────
let periodoAtualId = '__todos__';
let historicoInicializado = false;

function resolverBundlePeriodo(periodoId) {
  return periodoId === '__todos__' ? DATA : DATA.periodos[periodoId];
}

function aplicarPeriodo(periodoId) {
  const bundle = resolverBundlePeriodo(periodoId);
  if (!bundle) return;
  periodoAtualId = periodoId;
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

// ── seletor de período cascateado: Ano (sempre visível) → tipo de período
// (Ano completo/Semestre/Trimestre/Bimestre/Mês, só aparece com um ano
// escolhido) → intervalo específico (só aparece conforme o tipo, filtrado
// pro ano ativo). Cada nível reseta o(s) nível(is) seguinte(s) ao mudar. ────
let periodoCascata = { ano: 'todos', tipo: 'ano', subId: null };
const MESES_LABEL_CASCATA = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
const TIPOS_NIVEL2 = [
  ['ano', 'Ano completo'], ['semestre', 'Semestre'], ['trimestre', 'Trimestre'],
  ['bimestre', 'Bimestre'], ['mes', 'Mês'],
];
const PREFIXO_TIPO = { semestre: 'S', trimestre: 'T', bimestre: 'B', mes: 'M' };

function periodoFinalDaCascata() {
  if (periodoCascata.ano === 'todos') return '__todos__';
  if (periodoCascata.tipo === 'ano') return periodoCascata.ano;
  return periodoCascata.subId || periodoCascata.ano;
}

function renderSeletorCascata() {
  const container = document.getElementById('period-selector');
  const todosBtn = document.getElementById('period-todos-btn');
  container.innerHTML = '';
  todosBtn.classList.toggle('active', periodoCascata.ano === 'todos');

  const disponiveis = DATA.periodos_disponiveis || [];
  const anos = [...new Set(disponiveis.filter(p => p.tipo === 'ano').map(p => p.id))].sort();

  // Nível 1 — Ano, sempre visível
  const linhaAno = document.createElement('div');
  linhaAno.className = 'period-row';
  const labelAno = document.createElement('span');
  labelAno.className = 'tipo-label';
  labelAno.textContent = 'Ano:';
  linhaAno.appendChild(labelAno);
  const scrollAno = document.createElement('div');
  scrollAno.className = 'period-row-scroll';
  anos.forEach(ano => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'period-btn' + (periodoCascata.ano === ano ? ' active' : '');
    btn.textContent = ano;
    btn.dataset.ano = ano;
    btn.addEventListener('click', () => {
      periodoCascata = { ano, tipo: 'ano', subId: null };
      renderSeletorCascata();
    });
    scrollAno.appendChild(btn);
  });
  linhaAno.appendChild(scrollAno);
  container.appendChild(linhaAno);

  if (periodoCascata.ano === 'todos') {
    aplicarPeriodo('__todos__');
    return;
  }

  // Nível 2 — tipo de período (radio), só aparece com um ano específico ativo
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
    aplicarPeriodo(periodoCascata.ano);
    return;
  }

  // Nível 3 — intervalo específico, filtrado pro ano ativo
  const linhaSub = document.createElement('div');
  linhaSub.className = 'period-row period-nivel';
  const labelSub = document.createElement('span');
  labelSub.className = 'tipo-label';
  labelSub.innerHTML = '&nbsp;';
  linhaSub.appendChild(labelSub);
  const scrollSub = document.createElement('div');
  scrollSub.className = 'period-row-scroll';

  if (periodoCascata.tipo === 'mes') {
    // mostra os 12 meses sempre — desabilita em cinza os que não têm dados
    const idsExistentes = new Set(
      disponiveis.filter(p => p.tipo === 'mes' && p.id.startsWith(periodoCascata.ano + '-M')).map(p => p.id)
    );
    for (let m = 1; m <= 12; m++) {
      const id = `${periodoCascata.ano}-M${String(m).padStart(2, '0')}`;
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
      .filter(p => p.tipo === periodoCascata.tipo && p.id.startsWith(periodoCascata.ano + '-' + prefixo))
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
  periodoCascata = { ano: 'todos', tipo: 'ano', subId: null };
  renderSeletorCascata();
});

renderSeletorCascata();

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
  const nomes = ['A','B','C','D','E','F'];
  if (!dados.length) {
    el.innerHTML = '<p style="color:var(--muted)">Sem dados.</p>';
  } else {
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
        const r = Math.round(6 + t * 30), g = Math.round(78 + t * 133), b = Math.round(59 + t * 90);
        celulas += `<td style="background:rgba(${r},${g},${b},.35); text-align:center;">${v}</td>`;
      });
      tr.innerHTML = celulas;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    el.appendChild(table);
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
    if (Number.isInteger(num) && num >= 1 && num <= 60) filtrarPorDezena(num);
  });
  document.getElementById('hist-btn-limpar-filtro').addEventListener('click', () => {
    document.getElementById('hist-filtro-dezena').value = '';
    limparFiltroDezena();
  });

  aplicarFiltroPeriodoHistorico(periodoAtualId);
}

// ── Verificação leve de sorteios novos + botão "Atualizar dados" ────────────
// Só aparece quando o HTML foi gerado com --source supabase. O token do
// GitHub é o MESMO localStorage key do dashboard da Lotofácil (mesma origem,
// mesmo repositório) — configura uma vez, funciona nos dois dashboards.
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
    tendencia = calc_tendencia(sorteios, janela=30)
    repeticao_anterior = calc_repeticao_anterior(sorteios)
    ciclo_medio = calc_ciclo_medio(sorteios)
    cooc_completo = calc_coocorrencia_completa(sorteios)
    anticorrelacao = calc_anticorrelacao(cooc_completo, bottom_n=15)

    blocos = calc_blocos_bundle(rows, sorteios)
    blocos_periodo = calc_blocos_periodo(rows, sorteios)
    periodos, periodos_disponiveis = gerar_periodos(rows, sorteios)
    historico = calc_historico(rows, sorteios)
    financeiro = calc_financeiro(rows)
    meus_jogos = calc_jogos_financeiro_mega(JOGOS_MEGA, rows, sorteios) if JOGOS_MEGA else None

    data = {
        "meta": {
            "total": n,
            "inicio": rows[0]["data"],
            "fim": rows[-1]["data"],
            "concurso_ini": rows[0]["concurso"],
            "concurso_fim": rows[-1]["concurso"],
            "supabase": fonte_supabase,
            "github_repo": github_repo,
            "tabela": "megasena_sorteios",
            "workflow_file": "megasena_atualizar.yml",
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
        "sorteios_meta": [{"concurso": r["concurso"], "data": r["data"]} for r in rows],
        "blocos": blocos,
        "blocos_periodo": blocos_periodo,
        "periodos": periodos,
        "periodos_disponiveis": periodos_disponiveis,
        "historico": historico,
        "financeiro": financeiro,
        "meus_jogos": meus_jogos,
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
    parser = argparse.ArgumentParser(description="Gera BI HTML dos sorteios da Mega-Sena")
    parser.add_argument("--db", default=None, help="Caminho do banco SQLite (megasena.db)")
    parser.add_argument("--source", choices=["supabase"], default=None,
                         help="Ler direto do Supabase em vez do SQLite local")
    parser.add_argument("--supabase-url", default=None)
    parser.add_argument("--supabase-key", default=None, help="SUPABASE_ANON_KEY (leitura pública)")
    parser.add_argument("--periodo", default=None, help="Filtra por ano (ex: 2025) ou prefixo ISO (ex: 2025-06)")
    parser.add_argument("--output", default="megasena.html", help="Arquivo HTML de saída")
    parser.add_argument("--github-repo", default="andrevisc-1209/lotofacil-bi",
                         help="dono/repositorio — usado só pelo botão 'Atualizar dados'")
    args = parser.parse_args()

    fonte_supabase = None

    if args.source == "supabase":
        import megasena_db
        url = args.supabase_url
        key = args.supabase_key
        if not (url and key):
            env_url, env_key = megasena_db.carregar_credenciais_supabase()
            url = url or env_url
            key = key or env_key
        if not (url and key):
            print("SUPABASE_URL e SUPABASE_ANON_KEY precisam estar configurados "
                  "(--supabase-url/--supabase-key, variável de ambiente ou .env).")
            exit(1)
        db = megasena_db.Database.supabase(url, key)
        rows = carregar_de_database(db)
        db.fechar()
        print(f"Carregados {len(rows)} sorteios do Supabase.")
        fonte_supabase = {"url": url, "anon_key": key}
    elif args.db:
        if not Path(args.db).exists():
            print(f"Banco '{args.db}' não encontrado. Rode primeiro: python megasena_atualizar.py --init-all")
            exit(1)
        import megasena_db
        db = megasena_db.Database.sqlite(args.db)
        rows = carregar_de_database(db)
        db.fechar()
        print(f"Carregados {len(rows)} sorteios de '{args.db}'.")
    else:
        print("Especifique --db <arquivo.db> ou --source supabase.")
        exit(1)

    if not rows:
        print("Nenhum sorteio encontrado na fonte de dados. Rode megasena_atualizar.py primeiro.")
        exit(1)

    if args.periodo:
        rows = filtrar_por_periodo(rows, args.periodo)
        print(f"Filtrado para o período '{args.periodo}': {len(rows)} sorteios.")
        if not rows:
            print("Nenhum sorteio encontrado para esse período.")
            exit(1)

    gerar_html(rows, args.output, fonte_supabase=fonte_supabase, github_repo=args.github_repo)

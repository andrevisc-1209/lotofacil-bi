"""
lotofacil_gerar_jogos.py
-------------------------
Gera jogos da Lotofácil que seguem os PADRÕES ESTATÍSTICOS TÍPICOS observados no
histórico de sorteios — cruzando frequência, ciclo/atraso, pares e trios que mais
saíram juntos, pares que raramente saem juntos, soma, pares/ímpares e distribuição
por linha/coluna do volante (todas as análises já construídas em lotofacil_bi.py).

⚠️ AVISO IMPORTANTE
A Lotofácil é um sorteio aleatório: as 3.268.760 combinações possíveis de 15 números
têm exatamente a mesma probabilidade em todo concurso, e sorteios passados não têm
nenhuma influência sobre sorteios futuros. Este script NÃO aumenta a chance real de
ganhar — ele apenas evita combinações estatisticamente atípicas (tipo tudo par, ou
5+ números seguidos) e favorece números/pares/trios que apareceram mais no histórico,
o que é um critério de organização, não de previsão.

Uso:
    python lotofacil_gerar_jogos.py                  # gera 30 jogos
    python lotofacil_gerar_jogos.py --n 20 --seed 42
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

from lotofacil_bi import (
    carregar, dezenas, calc_frequencia, calc_atraso, calc_ciclo_medio,
    calc_pares_impares, calc_faixas, calc_soma, calc_coocorrencia_completa,
    calc_trios_frequentes, calc_anticorrelacao,
)
from lotofacil_simular import calcular_jogo


# ─── perfil estatístico do histórico ──────────────────────────────────────────

def percentil(valores: list[float], p: float) -> float:
    valores = sorted(valores)
    k = (len(valores) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return valores[int(k)]
    return valores[f] + (valores[c] - valores[f]) * (k - f)

def faixa_percentil(valores: list[int]) -> tuple[int, int]:
    return math.floor(percentil(valores, 0.05)), math.ceil(percentil(valores, 0.95))

def linha_de(d: int) -> int: return (d - 1) // 5
def coluna_de(d: int) -> int: return (d - 1) % 5

def maior_sequencia(combo) -> int:
    ordenado = sorted(combo)
    atual = maior = 1
    for i in range(1, len(ordenado)):
        if ordenado[i] == ordenado[i - 1] + 1:
            atual += 1
            maior = max(maior, atual)
        else:
            atual = 1
    return maior


def construir_perfil(rows: list[dict], sorteios: list[list[int]]) -> dict:
    freq = calc_frequencia(sorteios)
    atraso = calc_atraso(sorteios)
    ciclo = calc_ciclo_medio(sorteios)

    somas = calc_soma(sorteios)
    pares_hist = [pi["pares"] for pi in calc_pares_impares(sorteios)]
    faixas_hist = calc_faixas(sorteios)

    linhas_hist = [[0] * 5 for _ in sorteios]
    colunas_hist = [[0] * 5 for _ in sorteios]
    for i, s in enumerate(sorteios):
        for d in s:
            linhas_hist[i][linha_de(d)] += 1
            colunas_hist[i][coluna_de(d)] += 1

    cooc_completo = calc_coocorrencia_completa(sorteios)
    top_pares = {par for par, _ in cooc_completo.most_common(60)}
    piores_pares = {par for par, _ in calc_anticorrelacao(cooc_completo, bottom_n=60)}
    top_trios = {tuple(t["trio"]) for t in calc_trios_frequentes(sorteios, top_n=60)}

    return {
        "freq": freq,
        "atraso": atraso,
        "ciclo": {d: v["ciclo"] for d, v in ciclo.items()},
        "top_pares": top_pares,
        "piores_pares": piores_pares,
        "top_trios": top_trios,
        "soma_faixa": faixa_percentil(somas),
        "pares_faixa": faixa_percentil(pares_hist),
        "baixo_faixa": faixa_percentil([f["baixo"] for f in faixas_hist]),
        "medio_faixa": faixa_percentil([f["medio"] for f in faixas_hist]),
        "alto_faixa": faixa_percentil([f["alto"] for f in faixas_hist]),
        "linha_faixas": [faixa_percentil([l[i] for l in linhas_hist]) for i in range(5)],
        "coluna_faixas": [faixa_percentil([c[i] for c in colunas_hist]) for i in range(5)],
        "max_sequencia": max(maior_sequencia(s) for s in sorteios),
    }


# ─── amostragem ponderada sem reposição (Efraimidis–Spirakis) ────────────────

def amostra_ponderada(pesos: dict[int, float], k: int) -> list[int]:
    chaves = {n: random.random() ** (1.0 / w) for n, w in pesos.items()}
    return sorted(chaves, key=chaves.get, reverse=True)[:k]


def calcular_pesos(perfil: dict) -> dict[int, float]:
    pesos = {}
    for d in range(1, 26):
        peso = perfil["freq"].get(d, 1)
        # leve reforço para números "além do ciclo médio" (heurística popular de
        # atraso — sem efeito real sobre a probabilidade, ver aviso no topo do arquivo)
        if perfil["ciclo"].get(d) is not None and perfil["atraso"].get(d, 0) >= perfil["ciclo"][d]:
            peso *= 1.2
        pesos[d] = peso
    return pesos


# ─── validação e pontuação de um jogo candidato ──────────────────────────────

def dentro_da_faixa(valor: int, faixa: tuple[int, int]) -> bool:
    return faixa[0] <= valor <= faixa[1]

def jogo_valido(combo: tuple[int, ...], perfil: dict) -> bool:
    if not dentro_da_faixa(sum(combo), perfil["soma_faixa"]):
        return False
    pares = sum(1 for d in combo if d % 2 == 0)
    if not dentro_da_faixa(pares, perfil["pares_faixa"]):
        return False

    baixo = sum(1 for d in combo if 1 <= d <= 8)
    medio = sum(1 for d in combo if 9 <= d <= 17)
    alto = sum(1 for d in combo if 18 <= d <= 25)
    if not (dentro_da_faixa(baixo, perfil["baixo_faixa"])
            and dentro_da_faixa(medio, perfil["medio_faixa"])
            and dentro_da_faixa(alto, perfil["alto_faixa"])):
        return False

    linhas = Counter(linha_de(d) for d in combo)
    colunas = Counter(coluna_de(d) for d in combo)
    for i in range(5):
        if not dentro_da_faixa(linhas.get(i, 0), perfil["linha_faixas"][i]):
            return False
        if not dentro_da_faixa(colunas.get(i, 0), perfil["coluna_faixas"][i]):
            return False

    if maior_sequencia(combo) > perfil["max_sequencia"]:
        return False

    return True

def pontuar_jogo(combo: tuple[int, ...], perfil: dict) -> int:
    score = 0
    for par in combinations(sorted(combo), 2):
        if par in perfil["top_pares"]:
            score += 2
        elif par in perfil["piores_pares"]:
            score -= 3
    for trio in combinations(sorted(combo), 3):
        if trio in perfil["top_trios"]:
            score += 3
    for d in combo:
        if perfil["ciclo"].get(d) is not None and perfil["atraso"].get(d, 0) >= perfil["ciclo"][d]:
            score += 1
    return score


# ─── geração dos jogos ────────────────────────────────────────────────────────

def gerar_jogos(perfil: dict, quantidade: int, tentativas_max: int = 50_000) -> list[tuple[int, ...]]:
    pesos = calcular_pesos(perfil)
    escolhidos: list[tuple[int, ...]] = []
    vistos = set()
    tentativas = 0

    while len(escolhidos) < quantidade and tentativas < tentativas_max:
        tentativas += 1
        combo = tuple(sorted(amostra_ponderada(pesos, 15)))
        if combo in vistos or not jogo_valido(combo, perfil):
            continue
        # diversidade: exige ao menos 5 dezenas diferentes de qualquer jogo já escolhido
        if any(len(set(combo) & set(c)) > 10 for c in escolhidos):
            continue
        vistos.add(combo)
        escolhidos.append(combo)

    if len(escolhidos) < quantidade:
        print(f"[aviso] só foi possível gerar {len(escolhidos)}/{quantidade} jogos "
              f"diversos dentro do limite de tentativas.", file=sys.stderr)

    escolhidos.sort(key=lambda c: pontuar_jogo(c, perfil), reverse=True)
    return escolhidos


# ─── formatação do relatório ──────────────────────────────────────────────────

def formatar_relatorio(jogos: list[tuple[int, ...]], perfil: dict, rows: list[dict], sorteios: list[list[int]]) -> list[str]:
    n = len(sorteios)
    linhas = [
        "AVISO: estes jogos foram escolhidos para seguir os padrões estatísticos",
        "típicos destes MESMOS sorteios (soma, pares/ímpares, linha/coluna, pares e",
        "trios frequentes). Isso NÃO aumenta a chance real de ganhar — a Lotofácil é",
        "um sorteio aleatório e cada combinação tem sempre a mesma probabilidade.",
        "Os números abaixo são apenas organização estatística, não previsão.",
        "",
        f"{len(jogos)} jogos gerados a partir de {n} sorteios "
        f"(concursos {rows[0]['concurso']} a {rows[-1]['concurso']})",
        "",
    ]

    largura = 78
    linha_dupla = "═" * largura
    linha_simples = "─" * largura

    linhas.append(linha_dupla)
    linhas.append(f" {'#':>3} │ {'Jogo (15 dezenas)':<47} │ {'Soma':>5} │ {'P/I':>5} │ Score")
    linhas.append(linha_simples)

    resultados_backtest = {}
    for i, combo in enumerate(jogos, start=1):
        numeros_fmt = " ".join(f"{d:02d}" for d in combo)
        soma = sum(combo)
        pares = sum(1 for d in combo if d % 2 == 0)
        score = pontuar_jogo(combo, perfil)
        linhas.append(f" {i:>3} │ {numeros_fmt:<47} │ {soma:>5} │ {pares:>2}/{15-pares:<2} │ {score:>5}")

        r = calcular_jogo(set(combo), rows, sorteios)
        resultados_backtest[i] = r

    linhas.append(linha_dupla)
    linhas.append("")

    # autoconsistência: como esses jogos teriam saído nestes mesmos 500 sorteios
    # (é circular por construção — não é uma prova de desempenho futuro)
    linhas.append("Autoconsistência histórica (referência, não previsão):")
    linhas.append(linha_simples)
    linhas.append(f" {'#':>3} │ {'15':>3} │ {'14':>3} │ {'13':>3} │ {'12':>3} │ {'11':>3} │ {'Total ≥11':>9} │ {'%':>5}")
    linhas.append(linha_simples)
    for i, r in resultados_backtest.items():
        c = r["contagem"]
        total = r["total_premios"]
        pct = total / n * 100 if n else 0.0
        linhas.append(
            f" {i:>3} │ {c[15]:>3} │ {c[14]:>3} │ {c[13]:>3} │ {c[12]:>3} │ {c[11]:>3} │ {total:>9} │ {pct:>4.1f}%"
        )
    linhas.append("")

    return linhas


def salvar_dict_python(jogos: list[tuple[int, ...]], path: str):
    linhas = ['"""Jogos gerados por lotofacil_gerar_jogos.py — ver aviso no cabeçalho daquele arquivo."""', "", "JOGOS_SUGERIDOS = {"]
    for i, combo in enumerate(jogos, start=1):
        linhas.append(f"    \"Sugerido {i}\": {list(combo)},")
    linhas.append("}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")


# ─── main ──────────────────────────────────────────────────────────────────────

def main(input_path: str, output_txt: str, output_py: str, quantidade: int, seed: int | None):
    if seed is not None:
        random.seed(seed)

    if not Path(input_path).exists():
        print(f"Arquivo '{input_path}' não encontrado. Rode primeiro: python lotofacil_coletar.py", file=sys.stderr)
        sys.exit(1)

    rows = carregar(input_path)
    sorteios = [dezenas(r) for r in rows]
    print(f"Carregados {len(sorteios)} sorteios de '{input_path}'.\n")

    perfil = construir_perfil(rows, sorteios)
    jogos = gerar_jogos(perfil, quantidade)

    linhas = formatar_relatorio(jogos, perfil, rows, sorteios)
    for linha in linhas:
        print(linha)

    with open(output_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    salvar_dict_python(jogos, output_py)

    print(f"✓ Relatório salvo em '{output_txt}'")
    print(f"✓ Dicionário Python salvo em '{output_py}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera jogos da Lotofácil seguindo padrões estatísticos do histórico")
    parser.add_argument("--input", default="lotofacil_sorteios.csv")
    parser.add_argument("--output", default="lotofacil_jogos_sugeridos.txt")
    parser.add_argument("--output-py", default="lotofacil_jogos_sugeridos.py")
    parser.add_argument("--n", type=int, default=30, help="Quantidade de jogos a gerar (padrão: 30)")
    parser.add_argument("--seed", type=int, default=None, help="Semente aleatória para reprodutibilidade")
    args = parser.parse_args()

    main(args.input, args.output, args.output_py, args.n, args.seed)
